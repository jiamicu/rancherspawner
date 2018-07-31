"""
A Spawner for JupyterHub that runs each user's server in a separate docker container
"""

import os
import string
from pprint import pformat

from tornado import gen
from tornado.httpclient import AsyncHTTPClient 
from jupyterhub.spawner import Spawner
from .rancherapi import Client
from traitlets import (
    Dict,
    Unicode,
    Bool,
    Int,
    Any,
    default
)
''' Spawner

    Base class for spawning single-user notebook servers.

    Subclass this, and override the following methods:

    - load_state  
    - get_state  
    - start  
    - stop  
    - poll  
    As JupyterHub supports multiple users, an instance of the Spawner subclass is created for each user. If there are 20 JupyterHub users, there will be 20 instances of the subclass.'''

class RancherSpawner(Spawner):

    def load_state(self, state):
        """Restore state of spawner from database.

        Called for each user's spawner after the hub process restarts.

        `state` is a dict that'll contain the value returned by `get_state` of
        the spawner, or {} if the spawner hasn't persisted any state yet.

        Override in subclasses to restore any extra state that is needed to track
        the single-user server for that user. Subclasses should call super().
        """
        super(RancherSpawner, self).load_state(state)
        self.container_id = state.get('container_id', '')

    def get_state(self):
        """Save state of spawner into database.

        A black box of extra state for custom spawners. The returned value of this is
        passed to `load_state`.

        Subclasses should call `super().get_state()`, augment the state returned from
        there, and return that state.

        Returns
        -------
        state: dict
             a JSONable dict of state
        """
        state = super(RancherSpawner, self).get_state()
        if self.container_id:
            state['container_id'] = self.container_id
        return state

    @gen.coroutine
    def get_container(self):
        self.log.debug("Getting container '%s'", self.container_name)
        
        return container

    '''
    - fetch http://120.27.162.236:8080/v2-beta/projects get the envs if you have serval env please define name in configfile or by env
    - 
    '''

    @gen.coroutine
    def start(self, image=None, extra_create_kwargs=None,
        extra_start_kwargs=None, extra_host_config=None):
        """Start the single-user server

        Returns:
          (str, int): the (ip, port) where the Hub can connect to the server.

        .. versionchanged:: 0.7
            Return ip, port instead of setting on self.user.server directly.
        """
        container = yield self.get_container()

        if container is None:
            image = image or self.container_image
        
        else:
            pass
            

    @gen.coroutine
    def stop(self):
        """Stop the single-user server

        If `now` is set to `False`, do not wait for the server to stop. Otherwise, wait for
        the server to stop before returning.

        Must be a Tornado coroutine.
        """
        pass

    @gen.coroutine
    def poll(self):
        """Check if the single-user process is running

        Returns:
          None if single-user process is running.
          Integer exit status (0 if unknown), if it is not running.

        State transitions, behavior, and return response:

        - If the Spawner has not been initialized (neither loaded state, nor called start),
          it should behave as if it is not running (status=0).
        - If the Spawner has not finished starting,
          it should behave as if it is running (status=None).

        Design assumptions about when `poll` may be called:

        - On Hub launch: `poll` may be called before `start` when state is loaded on Hub launch.
          `poll` should return exit status 0 (unknown) if the Spawner has not been initialized via
          `load_state` or `start`.
        - If `.start()` is async: `poll` may be called during any yielded portions of the `start`
          process. `poll` should return None when `start` is yielded, indicating that the `start`
          process has not yet completed.

        """
        pass

import re
import tornado
from tornado.options import define, options
from tornado import web
from tornado.httpclient import AsyncHTTPClient

define("port", default=8888, help="run on the given port", type=int)
class MainHandler(tornado.web.RequestHandler):

    @gen.coroutine
    def get(self):
        client = Client(url='http://120.27.162.236:8080/v1',
                       access_key='A84AA229673BABAE01CC',
                       secret_key='wc4kqTydh9hG6muM8XL1vQ2P9tnWseMxUyMP62ys')
        # client.list_

if __name__ == '__main__':
    options.parse_command_line()
    application = web.Application([
        (r"/", MainHandler),
    ],debug=True)
    http_server = tornado.httpserver.HTTPServer(application)
    http_server.listen(options.port)
    tornado.ioloop.IOLoop.instance().start()
    