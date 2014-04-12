#!/usr/bin/python

import argparse
import SocketServer
import logging
import threading
import atexit
import signal
import sys
import json
import re
import unicodedata

DATA_GRID_MAX = 65535
REQUEST_MAX = 64
INPUT_MAX = 64

INT_RE = re.compile( '([0-9]+)' )
HEX_RE = re.compile( '(0x)?([0-9a-f]+)' )

class InvalidGridDataException( Exception ):
   pass

class InvalidGridCharException( Exception ):
   bad_char = ''
   def __init__( self, message, bad_char ):
      self.bad_char = bad_char
      Exception.__init__( self, message )

class HeartGridHandler( SocketServer.BaseRequestHandler ):

   logger = None
   listening = True

   def __init__( self, request, client_address, server ):
      
      self.logger = logging.getLogger( 'heartgrid.handler' )

      SocketServer.BaseRequestHandler.__init__(
         self, request, client_address, server
      )

   def handle( self ):
      
      self.logger.info(
         'Connection accepted from: {}'.format( self.client_address )
      )

      self.request.send( '{} bytes ram available.\n'.format( DATA_GRID_MAX ) )
      self.request.send( 'commands available: peek poke quit\n' )

      socket_file = self.request.makefile()
      while self.listening:
         self.request.send( 'ready> ' )

         # Accept commands.
         command_iter = socket_file.readline().strip().split( ' ' )

         self.logger.info( 'Client {} used "{}".'.format(
            self.request.getsockname(),
            command_iter
         ) )
         
         try:
            if 'quit' == command_iter[0].lower():
               
               self.listening = False

            elif 'poke' == command_iter[0].lower():

               if 3 > len( command_iter ):
                  # Invalid, show help.
                  self.request.send( 'usage: poke <address> <data>\n' )
               else:
                  self.server.grid_write(
                     HeartGridHandler.read_hex( command_iter[1] ),
                     command_iter[2]
                  )

            elif 'peek' == command_iter[0].lower():

               if 2 > len( command_iter ):
                  # Invalid, show help.
                  self.request.send( 'usage: peek <address> [length]\n' )
               else:
                  address = HeartGridHandler.read_hex( command_iter[1] )

                  if 3 <= len( command_iter ):
                     # User supplied a length, too.
                     length = HeartGridHandler.read_hex( command_iter[2] )
                  else:
                     length = 1

                  # Send back whatever we found.
                  value = self.server.grid_read( address, length )
                  if value:
                     self.request.send( value + '\n' )

         except InvalidGridCharException, e:
            self.logger.warn(
               'Client {} attempted to use non-printable character.'.format(
                  self.request.getsockname()
               )
            )
            self.request.send( e.message + '\n' )
         except InvalidGridDataException, e:
            self.request.send( e.message + '\n' )
         except ValueError, e:
            self.request.send( e.message + '\n' )

      self.logger.info( 'Client {} disconnected.'.format(
         self.request.getsockname()
      ) )

   @staticmethod
   def read_hex( data ):

      # See if data is a decimal.
      int_match = INT_RE.match( data )
      if int_match and len( int_match.groups()[0] ) == len( data ):
         return int( int_match.groups()[0] )

      # See if data is a hexidecimal.
      hex_match = HEX_RE.match( data.lower() )
      if hex_match:
         # Only add the length of the prefix if there is a prefix.
         if hex_match.groups()[0]:
            hex_len = \
               len( hex_match.groups()[0] ) + len( hex_match.groups()[1] )
         else:
            hex_len = len( hex_match.groups()[1] )

         # Only return if we used all the data.
         if len( data ) == hex_len:
            return int( hex_match.groups()[1], 16 )

      raise ValueError( 'unable to convert as decimal or hexidecimal.' )

class HeartGridServer( SocketServer.ThreadingMixIn, SocketServer.TCPServer ):

   daemon_threads = True
   allow_reuse_address = True

   logger = None
   data_grid = {}
   data_lock = threading.Lock()
   dump_path = None

   def __init__( self, server_address, dump_path=None ):
      
      self.logger = logging.getLogger( 'heartgrid.server' )

      if dump_path:
         self.dump_path = dump_path
         atexit.register( self.grid_dump, dump_path )
         
         # Load existing dump.
         self.logger.info( 'Loading dump "{}"...'.format( dump_path ) )
         try:
            with open( dump_path ) as dump_file:
               self.data_grid = json.loads( dump_file.read() )
            self.logger.info( 'Dump "{}" loaded with {} cells.'.format(
               dump_path, len( self.data_grid )
            ) )
         except IOError, e:
            self.logger.warn( 'Unable to load dump. Setting up new grid...' )
            self.data_grid = {str( i ) : 0 for i in range( DATA_GRID_MAX )}

      else:
         # Just setup a fresh grid.
         self.data_grid = {str( i ) : 0 for i in range( DATA_GRID_MAX )}

      # Handle interrupt/termination gracefully.
      signal.signal( signal.SIGINT, self.handle_interrupt )
      signal.signal( signal.SIGTERM, self.handle_terminate )
      signal.signal( signal.SIGUSR1, self.handle_user1 )
      
      SocketServer.TCPServer.__init__( self, server_address, HeartGridHandler )

   def serve_forever( self ):
      self.logger.info( 'Listening for incoming connections...' )
      SocketServer.TCPServer.serve_forever( self )

   def grid_write( self, address, data ):

      self.logger.debug( 'Writing {} to {}...'.format( data, address ) )

      if DATA_GRID_MAX <= address:
         raise InvalidGridDataException( 'address out of range.' )

      if INPUT_MAX <= len( data ):
         raise InvalidGridDataException( 'input length too long.' )

      # Make sure data is clean.
      for input_index_iter in range( len( data ) ):
         # Just let the exception handler trigger if something is off.
         self.sanitize_char(
            data[input_index_iter]
         )
      
      self.data_lock.acquire()

      # If data is longer than 1, split it into the next cell.
      data_index_iter = address
      for input_index_iter in range( len( data ) ):
         # Write the current cell.
         self.data_grid[str( data_index_iter )] = data[input_index_iter]

         # Increment the data index.
         data_index_iter += 1

         # Wrap the dest index if at the end of the grid.
         if DATA_GRID_MAX <= data_index_iter:
            data_index_iter = 0

      self.data_lock.release()

   def grid_read( self, address, length=1 ):

      self.logger.debug( 'Reading {}...'.format( address ) )

      if DATA_GRID_MAX <= address:
         raise InvalidGridDataException( 'address out of range.' )

      if REQUEST_MAX <= length:
         raise InvalidGridDataException( 'request length too long.' )

      self.data_lock.acquire()

      # Fetch from "length" cells.
      value = ''
      data_index_iter = address
      for out_index_iter in range( length ):
         # Read the current cell.
         if self.data_grid[str( data_index_iter )]:
            value += str( self.data_grid[str( data_index_iter )] )
         else:
            value += '0'
         
         # Increment the data index.
         data_index_iter += 1

         # Wrap the dest index if at the end of the grid.
         if DATA_GRID_MAX <= data_index_iter:
            data_index_iter = 0

      self.data_lock.release()

      return value

   def grid_dump( self, dump_path ):

      self.logger.debug( 'Saving dump to {}...'.format( dump_path ) )

      try:
         with open( dump_path, 'w' ) as dump_file:
            self.data_lock.acquire()
            dump_file.write( json.dumps( self.data_grid ) )
            self.data_lock.release()
      except IOError, e:
         self.logger.error( 'Error saving dump: {}'.format( e.message ) )

      self.logger.debug( 'Dump saved to {}.'.format( dump_path ) )

   def sanitize_char( self, char ):

      u_char = unicode( char )
      
      if unicodedata.category( u_char ) == 'Cc' or \
      unicodedata.category( u_char ) == 'Cf' or \
      unicodedata.category( u_char ) == 'Cs' or \
      unicodedata.category( u_char ) == 'Co' or \
      unicodedata.category( u_char ) == 'Cn' or \
      unicodedata.category( u_char ) == 'Zs' or \
      unicodedata.category( u_char ) == 'Zl' or \
      unicodedata.category( u_char ) == 'Zp':
         raise InvalidGridCharException(
            'unprintable character entered', u_char
         )
      else:
         return char

   def handle_interrupt( self, signal, frame ):
      
      self.logger.info( 'Interrupt received. Quitting...' )

      sys.exit( 0 )

   def handle_terminate( self, signal, frame ):
      
      self.logger.info( 'Terminate received. Quitting...' )

      sys.exit( 0 )

   def handle_user1( self, signal, frame ):
      
      self.logger.debug( 'USER1 received. Forcing dump...' )

      self.grid_dump( self.dump_path )

if '__main__' == __name__:

   parser = argparse.ArgumentParser()

   parser.add_argument(
      '-s', '--serve-address', action='store', dest='address', type=str,
      default='0.0.0.0',
      help='The address on which to listen for connections.'
   )
   parser.add_argument(
      '-p', '--port', action='store', dest='port', type=int, default=8025,
      help='The port on which to listen for connections.'
   )
   parser.add_argument(
      '-v', '--verbose', action='store_true', dest='verbose',
      help='Enable verbose debug output.'
   )
   parser.add_argument(
      '-d', '--dump-path', action='store', dest='dump_path',
      help='Load/save the memory to a dump file at dump_path.'
   )

   args = parser.parse_args()

   if args.verbose:
      logging.basicConfig( level=logging.DEBUG )
   else:
      logging.basicConfig( level=logging.INFO )

   server = HeartGridServer( (args.address, args.port), args.dump_path )
   server.serve_forever()

