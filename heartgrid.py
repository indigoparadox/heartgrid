#!/usr/bin/python

import argparse
import SocketServer
import logging
import threading

DATA_GRID_MAX = 65535
REQUEST_MAX = 64
INPUT_MAX = 64

# TODO: Strip "dangerous" (unprintable) characters.

class InvalidGridDataException( Exception ):
   pass

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

      socket_file = self.request.makefile()
      while self.listening:
         # Accept commands.
         command_iter = socket_file.readline().strip().split( ' ' )

         try:
            if 'quit' == command_iter[0].lower():
               self.listening = False
            elif 'poke' == command_iter[0].lower():
               if 3 > len( command_iter ):
                  self.request.send( 'usage: poke <address> <data>\n' )
               else:
                  # TODO: Maybe translate the first argument from hex.
                  self.server.grid_write(
                     int( command_iter[1] ), command_iter[2]
                  )
            elif 'peek' == command_iter[0].lower():
               if 2 > len( command_iter ):
                  self.request.send( 'usage: peek <address> [length]\n' )
               else:
                  # TODO: Maybe translate the arguments from hex.
                  address = int( command_iter[1] )

                  if 3 <= len( command_iter ):
                     # User supplied a length, too.
                     length = int( command_iter[2] )
                  else:
                     length = 1

                  # Send back whatever we found.
                  value = self.server.grid_read( address, length )
                  if value:
                     self.request.send( value + '\n' )

         except InvalidGridDataException, e:
            self.request.send( e.message + '\n' )
         except ValueError, e:
            self.request.send( e.message + '\n' )

class HeartGridServer( SocketServer.ThreadingMixIn, SocketServer.TCPServer ):

   daemon_threads = True
   allow_reuse_address = True

   logger = None
   data_grid = {}
   data_lock = threading.Lock()

   def __init__( self, server_address ):
      
      self.logger = logging.getLogger( 'heartgrid.server' )

      self.data_grid = {i : 0 for i in range( DATA_GRID_MAX )}
      
      SocketServer.TCPServer.__init__( self, server_address, HeartGridHandler )

   def grid_write( self, address, data ):
      if DATA_GRID_MAX <= address:
         raise InvalidGridDataException( 'address out of range.' )

      if INPUT_MAX <= len( data ):
         raise InvalidGridDataException( 'input length too long.' )

      self.data_lock.acquire()
      
      # If data is longer than 1, split it into the next cell.
      data_index_iter = address
      for src_index_iter in range( len( data ) ):
         # Write the current cell.
         self.data_grid[data_index_iter] = data[src_index_iter]

         # Increment the data index.
         data_index_iter += 1

         # Wrap the dest index if at the end of the grid.
         if DATA_GRID_MAX <= data_index_iter:
            data_index_iter = 0

      self.data_lock.release()

   def grid_read( self, address, length=1 ):
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
         if self.data_grid[data_index_iter]:
            value += str( self.data_grid[data_index_iter] )
         else:
            value += '0'
         
         # Increment the data index.
         data_index_iter += 1

         # Wrap the dest index if at the end of the grid.
         if DATA_GRID_MAX <= data_index_iter:
            data_index_iter = 0

      self.data_lock.release()

      return value

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

   args = parser.parse_args()

   if args.verbose:
      logging.basicConfig( level=logging.DEBUG )
   else:
      logging.basicConfig( level=logging.INFO )

   server = HeartGridServer( (args.address, args.port) )
   server.serve_forever()

