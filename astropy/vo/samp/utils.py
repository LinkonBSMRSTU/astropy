# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""Utility functions and classes"""

from __future__ import print_function, division

import base64
import hashlib
import inspect
import platform
import socket
import sys
import traceback
import warnings

from ...extern.six.moves import queue, input
from ...extern.six.moves import socketserver
from ...extern.six import StringIO, PY2, PY3

try:
    import bsddb
except ImportError:
    BDB_SUPPORT = False
else:
    BDB_SUPPORT = True

try:
    import ssl
except ImportError:
    SSL_SUPPORT = False
else:
    SSL_SUPPORT = True


PYTHON_VERSION = float(platform.python_version()[:3])

from ...extern.six.moves.http_client import HTTPConnection, HTTPS_PORT
from ...extern.six.moves.urllib.parse import parse_qs
from ...extern.six.moves.urllib.error import URLError
from ...extern.six.moves.urllib.request import urlopen
from ...extern.six.moves import xmlrpc_client as xmlrpc

if PY3:
    from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer
else:
    from SimpleXMLRPCServer import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

from .constants import SAMP_STATUS_ERROR, SAMP_ICON
from .errors import SAMPWarning
from ...config import ConfigurationItem

ALLOW_INTERNET = ConfigurationItem('use_internet', True,
                                   "Whether to allow astropy.vo.samp to use the internet, if available")


def internet_on():
    return False
    if not ALLOW_INTERNET():
        return False
    else:
        try:
            urlopen('http://google.com', timeout=1)
            return True
        except URLError:
            pass
        return False

__all__ = ["SAMPMsgReplierWrapper"]

__doctest_skip__ = ['.']


class _ServerProxyPoolMethod:
    # some magic to bind an XML-RPC method to an RPC server.
    # supports "nested" methods (e.g. examples.getStateName)

    def __init__(self, proxies, name):
        self.__proxies = proxies
        self.__name = name

    def __getattr__(self, name):
        return _ServerProxyPoolMethod(self.__proxies, "%s.%s" % (self.__name, name))

    def __call__(self, *args, **kwrds):
        proxy = self.__proxies.get()
        try:
            response = eval("proxy.%s(*args, **kwrds)" % self.__name)
        except:
            self.__proxies.put(proxy)
            raise
        self.__proxies.put(proxy)
        return response


class ServerProxyPool(object):
    """
    A thread-safe pool of `xmlrpc.ServerProxy` objects.
    """

    def __init__(self, size, proxy_class, *args, **keywords):

        self._proxies = queue.Queue(size)
        for i in range(size):
            self._proxies.put(proxy_class(*args, **keywords))

    def __getattr__(self, name):
        # magic method dispatcher
        return _ServerProxyPoolMethod(self._proxies, name)


def web_profile_text_dialog(request, queue):

    samp_name = "unknown"

    if isinstance(request[0], str):
        # To support the old protocol version
        samp_name = request[0]
    else:
        samp_name = request[0]["samp.name"]

    text = \
        """A Web application which declares to be

Name: %s
Origin: %s

is requesting to be registered with the SAMP Hub.
Pay attention that if you permit its registration, such
application will acquire all current user privileges, like
file read/write.

Do you give your consent? [yes|no]""" % (samp_name, request[2])

    print(text)
    answer = input(">>> ")
    queue.put(answer.lower() in ["yes", "y"])


class SAMPMsgReplierWrapper(object):
    """
    Decorator class/function that allows to automatically grab
    errors and returned maps (if any) from a function bound
    to a SAMP call (or notify).

    Parameters
    ----------
    cli : `SAMPIntegratedClient` or `SAMPClient`
        SAMP client instance.
        Decorator initialization, accepting the instance of the
        client that receives the call or notification.
    """

    def __init__(self, cli):
        self.cli = cli

    def __call__(self, f):

        def wrapped_f(*args):

            if ((inspect.ismethod(f) and f.__func__.__code__.co_argcount == 6)
                or (inspect.isfunction(f) and f.__code__.co_argcount == 5)
                    or args[2] is None):

                # It is a notification
                f(*args)

            else:
                # It's a call
                try:
                    result = f(*args)
                    if result:
                        self.cli.hub.reply(self.cli.get_private_key(), args[2],
                                           {"samp.status": SAMP_STATUS_ERROR,
                                            "samp.result": result})
                except:
                    err = StringIO()
                    traceback.print_exc(file=err)
                    txt = err.getvalue()
                    self.cli.hub.reply(self.cli.get_private_key(), args[2],
                                       {"samp.status": SAMP_STATUS_ERROR,
                                        "samp.result": {"txt": txt}})

        return wrapped_f


class SAMPSimpleXMLRPCRequestHandler(SimpleXMLRPCRequestHandler):
    """
    XMLRPC handler of Standar Profile requests (internal use only)
    """

    def do_GET(self):

        if self.path == '/samp/icon':
            self.send_response(200, 'OK')
            self.send_header('Content-Type', 'image/png')
            self.end_headers()
            self.wfile.write(SAMP_ICON)

    if PYTHON_VERSION >= 2.7:

        def do_POST(self):
            """
            Handles the HTTP POST request.

            Attempts to interpret all HTTP POST requests as XML-RPC calls,
            which are forwarded to the server's `_dispatch` method for handling.
            """

            # Check that the path is legal
            if not self.is_rpc_path_valid():
                self.report_404()
                return

            try:
                # Get arguments by reading body of request.
                # We read this in chunks to avoid straining
                # socket.read(); around the 10 or 15Mb mark, some platforms
                # begin to have problems (bug #792570).
                max_chunk_size = 10 * 1024 * 1024
                size_remaining = int(self.headers["content-length"])
                L = []
                while size_remaining:
                    chunk_size = min(size_remaining, max_chunk_size)
                    L.append(self.rfile.read(chunk_size))
                    size_remaining -= len(L[-1])
                data = b''.join(L)

                params, method = xmlrpc.loads(data)

                if method == "samp.webhub.register":
                    params = list(params)
                    params.append(self.client_address)
                    if 'Origin' in self.headers:
                        params.append(self.headers.get('Origin'))
                    else:
                        params.append('unknown')
                    params = tuple(params)
                    data = xmlrpc.dumps(params, methodname=method)

                elif method in ('samp.hub.notify', 'samp.hub.notifyAll',
                                'samp.hub.call', 'samp.hub.callAll',
                                'samp.hub.callAndWait'):

                    user = "unknown"

                    if 'Authorization' in self.headers:
                        # handle Basic authentication
                        (enctype, encstr) = self.headers.get('Authorization').split()
                        user, password = base64.standard_b64decode(encstr).split(':')

                    if method == 'samp.hub.callAndWait':
                        params[2]["host"] = self.address_string()
                        params[2]["user"] = user
                    else:
                        params[-1]["host"] = self.address_string()
                        params[-1]["user"] = user

                    data = xmlrpc.dumps(params, methodname=method)

                data = self.decode_request_content(data)
                if data is None:
                    return  # response has been sent

                # In previous versions of SimpleXMLRPCServer, _dispatch
                # could be overridden in this class, instead of in
                # SimpleXMLRPCDispatcher. To maintain backwards compatibility,
                # check to see if a subclass implements _dispatch and dispatch
                # using that method if present.
                response = self.server._marshaled_dispatch(
                    data, getattr(self, '_dispatch', None), self.path
                )
            except Exception as e:  # This should only happen if the module is buggy
                # internal error, report as HTTP server error
                self.send_response(500)

                # Send information about the exception if requested
                if hasattr(self.server, '_send_traceback_header') and \
                   self.server._send_traceback_header:
                    self.send_header("X-exception", str(e))
                    trace = traceback.format_exc()
                    trace = str(trace.encode('ASCII', 'backslashreplace'), 'ASCII')
                    self.send_header("X-traceback", trace)

                self.send_header("Content-length", "0")
                self.end_headers()
            else:
                # got a valid XML RPC response
                self.send_response(200)
                self.send_header("Content-type", "text/xml")
                if self.encode_threshold is not None:
                    if len(response) > self.encode_threshold:
                        q = self.accept_encodings().get("gzip", 0)
                        if q:
                            try:
                                response = xmlrpc.gzip_encode(response)
                                self.send_header("Content-Encoding", "gzip")
                            except NotImplementedError:
                                pass
                self.send_header("Content-length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

    else:

        def do_POST(self):
            """
            Handles the HTTP POST request.

            Attempts to interpret all HTTP POST requests as XML-RPC calls,
            which are forwarded to the server's `_dispatch` method for handling.
            """

            # Check that the path is legal
            if not self.is_rpc_path_valid():
                self.report_404()
                return

            try:
                # Get arguments by reading body of request.
                # We read this in chunks to avoid straining
                # socket.read(); around the 10 or 15Mb mark, some platforms
                # begin to have problems (bug #792570).
                max_chunk_size = 10 * 1024 * 1024
                size_remaining = int(self.headers["content-length"])
                L = []
                while size_remaining:
                    chunk_size = min(size_remaining, max_chunk_size)
                    L.append(self.rfile.read(chunk_size))
                    size_remaining -= len(L[-1])
                data = ''.join(L)

                params, method = xmlrpc.loads(data)

                if method == "samp.webhub.register":
                    params = list(params)
                    params.append(self.client_address)
                    if 'Origin' in self.headers:
                        params.append(self.headers.get('Origin'))
                    else:
                        params.append('unknown')
                    params = tuple(params)
                    data = xmlrpc.dumps(params, methodname=method)

                elif method in ('samp.hub.notify', 'samp.hub.notifyAll',
                                'samp.hub.call', 'samp.hub.callAll',
                                'samp.hub.callAndWait'):

                    user = "unknown"

                    if 'Authorization' in self.headers:
                        # handle Basic authentication
                        (enctype, encstr) = self.headers.get('Authorization').split()
                        user, password = base64.standard_b64decode(encstr).split(':')

                    if method == 'samp.hub.callAndWait':
                        params[2]["host"] = self.address_string()
                        params[2]["user"] = user
                    else:
                        params[-1]["host"] = self.address_string()
                        params[-1]["user"] = user

                    data = xmlrpc.dumps(params, methodname=method)

                # In previous versions of SimpleXMLRPCServer, _dispatch
                # could be overridden in this class, instead of in
                # SimpleXMLRPCDispatcher. To maintain backwards compatibility,
                # check to see if a subclass implements _dispatch and dispatch
                # using that method if present.
                response = self.server._marshaled_dispatch(
                    data, getattr(self, '_dispatch', None)
                )
            except Exception as e:  # This should only happen if the module is buggy
                # internal error, report as HTTP server error
                self.send_response(500)

                # Send information about the exception if requested
                if hasattr(self.server, '_send_traceback_header') and \
                   self.server._send_traceback_header:
                    self.send_header("X-exception", str(e))
                    self.send_header("X-traceback", traceback.format_exc())

                self.end_headers()
            else:
                # got a valid XML RPC response
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

                # shut down the connection
                self.wfile.flush()
                self.connection.shutdown(1)


class ThreadingXMLRPCServer(socketserver.ThreadingMixIn, SimpleXMLRPCServer):
    """
    Asynchronous multithreaded XMLRPC server (internal use only)
    """

    def __init__(self, addr, log=None, requestHandler=SAMPSimpleXMLRPCRequestHandler,
                 logRequests=True, allow_none=True, encoding=None):
        self.log = log
        SimpleXMLRPCServer.__init__(self, addr, requestHandler,
                                    logRequests, allow_none, encoding)

    def handle_error(self, request, client_address):
        if self.log is None:
            socketserver.BaseServer.handle_error(self, request, client_address)
        else:
            warnings.warn("Exception happened during processing of request from %s: %s" % (client_address, sys.exc_info()[1]), SAMPWarning)


class WebProfileRequestHandler(SAMPSimpleXMLRPCRequestHandler):
    """
    Handler of XMLRPC requests performed through the WebProfile (internal use
    only)
    """

    def _send_CORS_header(self):

        if not self.headers.get('Origin') is None:

            method = self.headers.get('Access-Control-Request-Method')
            if method and self.command == "OPTIONS":
                # Preflight method
                self.send_header('Content-Length', '0')
                self.send_header('Access-Control-Allow-Origin', self.headers.get('Origin'))
                self.send_header('Access-Control-Allow-Methods', method)
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.send_header('Access-Control-Allow-Credentials', 'true')
            else:
                # Simple method
                self.send_header('Access-Control-Allow-Origin', self.headers.get('Origin'))
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.send_header('Access-Control-Allow-Credentials', 'true')

    def end_headers(self):
        self._send_CORS_header()
        SAMPSimpleXMLRPCRequestHandler.end_headers(self)

    def _serve_cross_domain_xml(self):

        cross_domain = False

        if self.path == "/crossdomain.xml":
            # Adobe standard
            response = """<?xml version='1.0'?>
<!DOCTYPE cross-domain-policy SYSTEM "http://www.adobe.com/xml/dtds/cross-domain-policy.dtd">
<cross-domain-policy>
  <site-control permitted-cross-domain-policies="all"/>
  <allow-access-from domain="*"/>
  <allow-http-request-headers-from domain="*" headers="*"/>
</cross-domain-policy>"""

            self.send_response(200, 'OK')
            self.send_header('Content-Type', 'text/x-cross-domain-policy')
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            self.wfile.flush()
            cross_domain = True

        elif self.path == "/clientaccesspolicy.xml":
            # Microsoft standard
            response = """<?xml version='1.0'?>
<access-policy>
  <cross-domain-access>
    <policy>
      <allow-from>
        <domain uri="*"/>
      </allow-from>
      <grant-to>
        <resource path="/" include-subpaths="true"/>
      </grant-to>
    </policy>
  </cross-domain-access>
</access-policy>"""

            self.send_response(200, 'OK')
            self.send_header('Content-Type', 'text/xml')
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            self.wfile.flush()
            cross_domain = True

        return cross_domain

    def do_POST(self):
        if self._serve_cross_domain_xml():
            return

        return SAMPSimpleXMLRPCRequestHandler.do_POST(self)

    def do_HEAD(self):

        if not self.is_http_path_valid():
            self.report_404()
            return

        if self._serve_cross_domain_xml():
            return

    def do_OPTIONS(self):

        self.send_response(200, 'OK')
        self.end_headers()

    def do_GET(self):

        if not self.is_http_path_valid():
            self.report_404()
            return

        split_path = self.path.split('?')

        if split_path[0] in ['/translator/%s' % clid for clid in self.server.clients]:
            # Request of a file proxying
            urlpath = parse_qs(split_path[1])
            try:
                proxyfile = urlopen(urlpath["ref"][0])
                self.send_response(200, 'OK')
                self.end_headers()
                self.wfile.write(proxyfile.read())
                proxyfile.close()
            except:
                self.report_404()
                return

        if self._serve_cross_domain_xml():
            return

    def is_http_path_valid(self):

        valid_paths = ["/clientaccesspolicy.xml", "/crossdomain.xml"] + ['/translator/%s' % clid for clid in self.server.clients]
        return self.path.split('?')[0] in valid_paths


class WebProfileXMLRPCServer(ThreadingXMLRPCServer):
    """
    XMLRPC server supporting the SAMP Web Profile
    """

    def __init__(self, addr, log=None, requestHandler=WebProfileRequestHandler,
                 logRequests=True, allow_none=True, encoding=None):

        self.clients = []
        ThreadingXMLRPCServer.__init__(self, addr, log, requestHandler,
                                       logRequests, allow_none, encoding)

    def add_client(self, client_id):
        self.clients.append(client_id)

    def remove_client(self, client_id):
        try:
            self.clients.remove(client_id)
        except:
            pass


if SSL_SUPPORT:

    class HTTPSConnection(HTTPConnection):
        """
        This class allows communication via SSL (client side - internal use
        only).
        """

        default_port = HTTPS_PORT

        def __init__(self, host, port=None, key_file=None, cert_file=None,
                     cert_reqs=ssl.CERT_NONE, ca_certs=None,
                     ssl_version=ssl.PROTOCOL_SSLv3, strict=None):

            HTTPConnection.__init__(self, host, port, strict)

            self.key_file = key_file
            self.cert_file = cert_file
            self.cert_reqs = cert_reqs
            self.ca_certs = ca_certs
            self.ssl_version = ssl_version

        def connect(self):
            "Connect to a host on a given (SSL) port."

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((self.host, self.port))
            sslconn = ssl.wrap_socket(sock, server_side=False,
                                      cert_file=self.cert_file,
                                      key_file=self.key_file,
                                      cert_reqs=self.cert_reqs,
                                      ca_certs=self.ca_certs,
                                      ssl_version=self.ssl_version)
            self.sock = sslconn

    if PY2:

        from ...extern.six.moves.http_client import HTTP

        class HTTPS(HTTP):
            """
            Facility class fo HTTP communication (internal use only)
            """

            _connection_class = HTTPSConnection

            def __init__(self, host='', port=None, key_file=None, cert_file=None,
                         cert_reqs=ssl.CERT_NONE, ca_certs=None,
                         ssl_version=ssl.PROTOCOL_SSLv3):

                # provide a default host, pass the X509 cert info

                # urf. compensate for bad input.
                if port == 0:
                    port = None

                self._setup(self._connection_class(host, port, key_file,
                                                   cert_file, cert_reqs,
                                                   ca_certs, ssl_version, None))

                # we never actually use these for anything, but we keep them
                # here for compatibility with post-1.5.2 CVS.
                self.key_file = key_file
                self.cert_file = cert_file

            def getresponse(self, buffering=False):
                "Get the response from the server."
                return self._conn.getresponse(buffering)

    class SafeTransport(xmlrpc.Transport):
        """
        Handles an HTTPS transaction to an XML-RPC server. (internal use only)
        """

        def __init__(self, key_file=None, cert_file=None,
                     cert_reqs=ssl.CERT_NONE, ca_certs=None,
                     ssl_version=ssl.PROTOCOL_SSLv3, strict=None,
                     use_datetime=0):

            xmlrpc.Transport.__init__(self, use_datetime)
            self._connection = (None, None)
            self.key_file = key_file
            self.cert_file = cert_file
            self.cert_reqs = cert_reqs
            self.ca_certs = ca_certs
            self.ssl_version = ssl_version

        def make_connection(self, host):

            if self._connection and host == self._connection[0]:
                return self._connection[1]

            # create a HTTPS connection object from a host descriptor
            # host may be a string, or a (host, x509-dict) tuple
            host, extra_headers, x509 = self.get_host_info(host)
            if PY2:
                return HTTPS(host, None, self.key_file, self.cert_file,
                             self.cert_reqs, self.ca_certs, self.ssl_version)
            else:
                from ...extern.six.moves.http_client import HTTPSConnection
                self._connection = host, HTTPSConnection(host, None, **(x509 or {}))
                return self._connection[1]

    class SecureXMLRPCServer(ThreadingXMLRPCServer):

        """
        An XMLRPC server supporting secure sockets connections (internal use only)
        """

        def __init__(self, addr, key_file, cert_file, cert_reqs, ca_certs, ssl_version,
                     log=None, requestHandler=SimpleXMLRPCRequestHandler,
                     logRequests=True, allow_none=True, encoding=None):
            """
            Secure XML-RPC server.

            It it very similar to SimpleXMLRPCServer but it uses HTTPS for transporting XML data.
            """
            self.key_file = key_file
            self.cert_file = cert_file
            self.cert_reqs = cert_reqs
            self.ca_certs = ca_certs
            self.ssl_version = ssl_version
            self.allow_reuse_address = True

            ThreadingXMLRPCServer.__init__(self, addr, log, requestHandler,
                                           logRequests, allow_none, encoding)

        def get_request(self):
            # override this to wrap socket with SSL
            sock, addr = self.socket.accept()
            sslconn = ssl.wrap_socket(sock, server_side=True,
                                      certfile=self.cert_file,
                                      keyfile=self.key_file,
                                      cert_reqs=self.cert_reqs,
                                      ca_certs=self.ca_certs,
                                      ssl_version=self.ssl_version)
            return sslconn, addr


if BDB_SUPPORT:

    class BasicAuthSimpleXMLRPCRequestHandler(SAMPSimpleXMLRPCRequestHandler):
        """
        XML-RPC Request Handler for Basic Authentication support. (internal use only)

        Paramters
        ---------
        auth_file : str
            Authentication file path. It is a Berkeley DB file in Hash
            format containing a set of key=value pairs of the form:
            `<user name>=md5(<password>)<group 1>,<group 2>,<group 3>,...`.

        access_restrict : dict
            Dictionary containing the restriction rules for authentication.
            If the access must be restricted to a specific user then `access_restrict` is a dictionary
            containing `{"user"; <user name>}`. If the access must be restricted to the
            users belonging to a certain group, the `access_restrict` is a dictionary containing
            `{"group"; <group name>}`. An additional key can be present: `"admin": <administrator user>`.
            It defines the name of the administrator user with full access permission.
        """

        def __init__(self, request, client_address, server, auth_file, access_restrict=None):
            self.db = bsddb.hashopen(auth_file, "r")
            self.access_restrict = access_restrict
            SimpleXMLRPCRequestHandler.__init__(self, request, client_address, server)
            self.db.close()

        def checkId(self, id, pwd):

            if id in self.db.keys():

                pwdhash = self.db[id][0:16]
                groups = self.db[id][16:]
                pwd = hashlib.md5(pwd.encode('utf-8')).digest()

                if self.access_restrict is not None:

                    # ADMIN TEST
                    if "admin" in self.access_restrict:
                        admin = self.access_restrict["admin"]
                        if admin in self.db:
                            adminpwdhash = self.db[admin][0:16]
                            if admin == id and adminpwdhash == pwd:
                                return True

                    # TEST USER RESTRICTION
                    if "user" in self.access_restrict:
                        if self.access_restrict["user"] == id and pwdhash == pwd:
                            return True
                        else:
                            return False

                    # TEST GROUP RESTRICTION
                    if "group" in self.access_restrict:
                        if self.access_restrict["group"] in groups.split(",") and pwdhash == pwd:
                            return True
                        else:
                            return False
                else:
                    if pwdhash == pwd:
                        return True
                    else:
                        return False
            else:
                return False

        def authenticate_client(self):
            validuser = False

            if 'Authorization' in self.headers:
                # handle Basic authentication
                (enctype, encstr) = self.headers.get('Authorization').split()
                (user, password) = base64.standard_b64decode(encstr).split(':')
                validuser = self.checkId(user, password)

            return validuser

        def do_POST(self):

            if self.authenticate_client():
                SAMPSimpleXMLRPCRequestHandler.do_POST(self)
            else:
                self.report_401()

        def report_401(self):
            # Report a 401 error
            self.send_response(401)
            self.send_header("WWW-Authenticate", "Basic realm=\"Protected access\"")
            self.end_headers()
            # shut down the connection
            self.connection.shutdown(1)
            self.connection.close()

    class BasicAuthXMLRPCServer(ThreadingXMLRPCServer):
        """
        XML-RPC server with Basic Authentication support. (internal use only).

        Parameters
        ----------
        auth_file : str
            Authentication file path. It is a Berkeley DB file in Hash
            format containing a set of key=value pairs of the form:
            `<user name>=md5(<password>)<group 1>,<group 2>,<group 3>,...`.

        access_restrict : dict
            Dictionary containing the restriction rules for authentication.
            If the access must be restricted to a specific user then access_restrict is a dictionary
            containing `{"user"; <user name>}`. If the access must be restricted to the
            users belonging to a certain group, the access_restrict is a dictionary containing
            `{"group"; <group name>}`. An additional key can be present: `"admin": <administrator user>`.
            It defines the name of the administrator user with full access permission.
        """

        def __init__(self, addr, auth_file, access_restrict=None, log=None,
                     requestHandler=BasicAuthSimpleXMLRPCRequestHandler,
                     logRequests=True, allow_none=True, encoding=None):

            self.auth_file = auth_file
            self.access_restrict = access_restrict

            ThreadingXMLRPCServer.__init__(self, addr, log, requestHandler,
                                           logRequests, allow_none, encoding)

        def finish_request(self, request, client_address):
            if self.auth_file is not None and self.RequestHandlerClass == BasicAuthSimpleXMLRPCRequestHandler:
                self.RequestHandlerClass(request, client_address, self,
                                         self.auth_file, self.access_restrict)
            else:
                ThreadingXMLRPCServer.finish_request(self, request, client_address)

if SSL_SUPPORT and BDB_SUPPORT:

    class BasicAuthSecureXMLRPCServer(ThreadingXMLRPCServer):
        """
        XML-RPC server with Basic Authentication support, secure socket
        connections and multithreaded. (internal use only)
        """

        def __init__(self, addr, key_file, cert_file, cert_reqs, ca_certs, ssl_version,
                     auth_file, access_restrict=None, log=None,
                     requestHandler=BasicAuthSimpleXMLRPCRequestHandler,
                     logRequests=True, allow_none=True, encoding=None):

            self.key_file = key_file
            self.cert_file = cert_file
            self.cert_reqs = cert_reqs
            self.ca_certs = ca_certs
            self.ssl_version = ssl_version
            self.allow_reuse_address = True
            self.auth_file = auth_file
            self.access_restrict = access_restrict

            ThreadingXMLRPCServer.__init__(self, addr, log, requestHandler,
                                           logRequests, allow_none, encoding)

        def get_request(self):
            # override this to wrap socket with SSL
            sock, addr = self.socket.accept()
            sslconn = ssl.wrap_socket(sock, server_side=True,
                                      cert_file=self.cert_file,
                                      key_file=self.key_file,
                                      cert_reqs=self.cert_reqs,
                                      ca_certs=self.ca_certs,
                                      ssl_version=self.ssl_version)
            return sslconn, addr

        def finish_request(self, request, client_address):
            if self.auth_file is not None and self.RequestHandlerClass == BasicAuthSimpleXMLRPCRequestHandler:
                self.RequestHandlerClass(request, client_address, self,
                                         self.auth_file, self.access_restrict)
            else:
                ThreadingXMLRPCServer.finish_request(self, request, client_address)


class _HubAsClient(object):

    def __init__(self, handler):
        self._handler = handler

    def __getattr__(self, name):
        # magic method dispatcher
        return _HubAsClientMethod(self._handler, name)


class _HubAsClientMethod(object):

    def __init__(self, send, name):
        self.__send = send
        self.__name = name

    def __getattr__(self, name):
        return _HubAsClientMethod(self.__send, "%s.%s" % (self.__name, name))

    def __call__(self, *args):
        return self.__send(self.__name, args)
