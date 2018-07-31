from __future__ import print_function
import six
import re
import requests
import collections
import hashlib
import os
import json
import time
from tornado.httpclient import HTTPRequest, AsyncHTTPClient
from tornado.httputil import url_concat
from tornado import gen


def _prefix(cmd):
    prefix = os.path.basename(cmd.replace('-', '_'))
    for i in ['.pyc', '.py', '-cli', '-tool', '-util']:
        prefix = prefix.replace(i, '')
    return prefix.upper()

PREFIX = _prefix(__file__)
CACHE_DIR = '~/.' + PREFIX.lower()
TIME = not os.environ.get('TIME_API') is None

LIST = 'list-'
CREATE = 'create-'
UPDATE = 'update-'
DELETE = 'delete-'
ACTION = 'action-'
TRIM = True
JSON = False

GET_METHOD = 'GET'
POST_METHOD = 'POST'
PUT_METHOD = 'PUT'
DELETE_METHOD = 'DELETE'

HEADERS = {'Accept': 'application/json'}

LIST_METHODS = {'__iter__': True, '__len__': True, '__getitem__': True}


def echo(fn):
    def wrapped(*args, **kw):
        ret = fn(*args, **kw)
        print(fn.__name__, repr(ret))
        return ret
    return wrapped


def timed_url(fn):
    def wrapped(*args, **kw):
        if TIME:
            start = time.time()
            ret = fn(*args, **kw)
            delta = time.time() - start
            print(delta, args[1], fn.__name__)
            return ret
        else:
            return fn(*args, **kw)
    return wrapped


class RestObject:
    def __init__(self):
        pass

    @staticmethod
    def _is_public(k, v):
        return k not in ['links', 'actions', 'id', 'type'] and not callable(v)

    def __str__(self):
        return self.__repr__()

    def _as_table(self):
        if not hasattr(self, 'type'):
            return str(self.__dict__)
        data = [('Type', 'Id', 'Name', 'Value')]
        for k, v in six.iteritems(self):
            if self._is_public(k, v):
                if v is None:
                    v = 'null'
                if v is True:
                    v = 'true'
                if v is False:
                    v = 'false'
                v = str(v)
                if TRIM and len(v) > 70:
                    v = v[0:70] + '...'
                data.append((self.type, self.id, str(k), v))

        return indent(data, hasHeader=True, prefix='| ', postfix=' |',
                      wrapfunc=lambda x: str(x))

    def _is_list(self):
        return 'data' in self.__dict__ and isinstance(self.data, list)

    def __repr__(self):
        data = {}
        for k, v in six.iteritems(self.__dict__):
            if self._is_public(k, v):
                data[k] = v
        return repr(data)

    def __getattr__(self, k):
        if self._is_list() and k in LIST_METHODS:
            return getattr(self.data, k)
        return getattr(self.__dict__, k)

    def __iter__(self):
        if self._is_list():
            return iter(self.data)


class Schema(object):
    def __init__(self, text, obj):
        self.text = text
        self.types = {}
        for t in obj:
            if t.type != 'schema':
                continue

            self.types[t.id] = t
            t.creatable = False
            try:
                if POST_METHOD in t.collectionMethods:
                    t.creatable = True
            except:
                pass

            t.updatable = False
            try:
                if PUT_METHOD in t.resourceMethods:
                    t.updatable = True
            except:
                pass

            t.deletable = False
            try:
                if DELETE_METHOD in t.resourceMethods:
                    t.deletable = True
            except:
                pass

            t.listable = False
            try:
                if GET_METHOD in t.collectionMethods:
                    t.listable = True
            except:
                pass

            if not hasattr(t, 'collectionFilters'):
                t.collectionFilters = {}

    def __str__(self):
        return str(self.text)

    def __repr(self):
        return repr(self.text)


class ApiError(Exception):
    def __init__(self, obj):
        self.error = obj
        try:
            msg = '{} : {}\n{}'.format(obj.code, obj.message, obj)
            super(ApiError, self).__init__(self, msg)
        except:
            super(ApiError, self).__init__(self, 'API Error')


class ClientApiError(Exception):
    pass


class Client(object):
    def __init__(self, access_key=None, secret_key=None, url=None, cache=False,
                 cache_time=86400, strict=False, headers=HEADERS, **kw):
        self._headers = headers
        self._access_key = access_key
        self._secret_key = secret_key
        self._auth = (self._access_key, self._secret_key)
        self._url = url
        self._cache = cache
        self._cache_time = cache_time
        self._strict = strict
        self.schema = None
        self._session = requests.Session()

        if not self._cache_time:
            self._cache_time = 60 * 60 * 24  # 24 Hours

    @gen.coroutine
    def load_schemas(self):
        yield self._load_schemas()

    def valid(self):
        return self._url is not None and self.schema is not None

    def object_hook(self, obj):
        if isinstance(obj, list):
            return [self.object_hook(x) for x in obj]

        if isinstance(obj, dict):
            result = RestObject()

            for k, v in six.iteritems(obj):
                setattr(result, k, self.object_hook(v))

            for link in ['next', 'prev']:
                try:
                    url = getattr(result.pagination, link)
                    if url is not None:
                        setattr(result, link, lambda url=url: self._get(url))
                except AttributeError:
                    pass

            if hasattr(result, 'type') and isinstance(getattr(result, 'type'),
                                                      six.string_types):
                if hasattr(result, 'links'):
                    for link_name, link in six.iteritems(result.links):
                        cb = lambda _link=link, **kw: self._get(_link,
                                                                data=kw)
                        if hasattr(result, link_name):
                            setattr(result, link_name + '_link', cb)
                        else:
                            setattr(result, link_name, cb)

                if hasattr(result, 'actions'):
                    for link_name, link in six.iteritems(result.actions):
                        cb = lambda _link_name=link_name, _result=result, \
                            *args, **kw: self.action(_result, _link_name,
                                                     *args, **kw)
                        if hasattr(result, link_name):
                            setattr(result, link_name + '_action', cb)
                        else:
                            setattr(result, link_name, cb)

            return result

        return obj

    def object_pairs_hook(self, pairs):
        ret = collections.OrderedDict()
        for k, v in pairs:
            ret[k] = v
        return self.object_hook(ret)

    @gen.coroutine
    def _get(self, url, data=None):
        r = yield self._get_raw(url, data=data)
        return self._unmarshall(r)

    def _error(self, text):
        raise ApiError(self._unmarshall(text))

    @timed_url
    @gen.coroutine
    def _get_raw(self, url, data=None):
        r = yield self._get_response(url, data)
        return r.body

    @gen.coroutine
    def _fetch(self, request, callback=None, raise_error=True, **kwargs):
        
        try:
            response = yield self._httpclient.fetch(request, callback=None, raise_error=True, **kwargs)
        except Exception as e:
            print("Error: %s" % e)
        else:
            return response

    @gen.coroutine
    def _get_response(self, url, data=None):
        if data:
            url = url_concat(url, data)

        request = HTTPRequest(url, method=GET_METHOD, headers=self._headers, body=None, connect_timeout=5, request_timeout=5)
        r = yield self._fetch(request)
        if r.code < 200 or r.code >= 300:
            self._error(r.body)

        return r

    @timed_url
    @gen.coroutine
    def _post(self, url, data=None):
        request = HTTPRequest(url, method=POST_METHOD, headers=self._headers, body=self._marshall(data), connect_timeout=5, request_timeout=5)
        r = yield self._fetch(request)
        if r.code < 200 or r.code >= 300:
            self._error(r.body)

        return self._unmarshall(r.body)

    @timed_url
    @gen.coroutine
    def _put(self, url, data=None):
        request = HTTPRequest(url, method=PUT_METHOD, headers=self._headers, body=self._marshall(data), connect_timeout=5, request_timeout=5)
        r = yield self._fetch(request)
        if r.code < 200 or r.code >= 300:
            self._error(r.body)

        return self._unmarshall(r.body)

    @timed_url
    @gen.coroutine
    def _delete(self, url):
        request = HTTPRequest(url, method=DELETE_METHOD, headers=self._headers, connect_timeout=5, request_timeout=5)
        r = yield self._fetch(request)
        if r.code < 200 or r.code >= 300:
            self._error(r.body)

        return self._unmarshall(r.body)

    def _unmarshall(self, text):
        if text is None or text == '':
            return text
        obj = json.loads(text, object_hook=self.object_hook,
                         object_pairs_hook=self.object_pairs_hook)
        return obj

    def _marshall(self, obj, indent=None, sort_keys=False):
        if obj is None:
            return None
        return json.dumps(self._to_dict(obj), indent=indent, sort_keys=True)

    @gen.coroutine
    def _load_schemas(self, force=False):
        if self.schema and not force:
            return

        schema_text = self._get_cached_schema()

        if force or not schema_text:
            response = yield self._get_response(self._url)
            schema_url = response.headers.get('X-API-Schemas')
            if schema_url is not None and self._url != schema_url:
                schema_text = yield self._get_raw(schema_url)
            else:
                schema_text = response.body
            self._cache_schema(schema_text)

        obj = self._unmarshall(schema_text)

        schema = Schema(schema_text, obj)

        if len(schema.types) > 0:
            self._bind_methods(schema)
            self.schema = schema

    def reload_schema(self):
        yield self._load_schemas(force=True)

    @gen.coroutine
    def by_id(self, type, id, **kw):
        id = str(id)
        url = self.schema.types[type].links.collection
        if url.endswith('/'):
            url += id
        else:
            url = '/'.join([url, id])
        try:
            r = yield self._get(url, self._to_dict(**kw))
            return r
        except ApiError as e:
            if e.error.status == 404:
                return None
            else:
                raise e

    @gen.coroutine
    def update_by_id(self, type, id, *args, **kw):
        url = self.schema.types[type].links.collection
        if url.endswith('/'):
            url = url + id
        else:
            url = '/'.join([url, id])

        r = yield self._put_and_retry(url, *args, **kw)
        return r

    @gen.coroutine
    def update(self, obj, *args, **kw):
        url = obj.links.self
        r = yield self._put_and_retry(url, *args, **kw)
        return r

    @gen.coroutine
    def _put_and_retry(self, url, *args, **kw):
        retries = kw.get('retries', 3)
        last_error = None
        for i in range(retries):
            try:
                r = yield self._put(url, data=self._to_dict(*args, **kw))
                return r
            except ApiError as e:
                if e.error.status == 409:
                    last_error = e
                    time.sleep(.1)
                else:
                    raise e
        raise last_error

    @gen.coroutine
    def _post_and_retry(self, url, *args, **kw):
        retries = kw.get('retries', 3)
        last_error = None
        for i in range(retries):
            try:
                r = yield self._post(url, data=self._to_dict(*args, **kw))
                return r
            except ApiError as e:
                if e.error.status == 409:
                    last_error = e
                    time.sleep(.1)
                else:
                    raise e
        raise last_error

    def _validate_list(self, type, **kw):
        if not self._strict:
            return

        collection_filters = self.schema.types[type].collectionFilters

        for k in kw:
            if hasattr(collection_filters, k):
                return

            for filter_name, filter_value in six.iteritems(collection_filters):
                for m in filter_value.modifiers:
                    if k == '_'.join([filter_name, m]):
                        return

            raise ClientApiError(k + ' is not searchable field')

    @gen.coroutine
    def list(self, type, **kw):
        if type not in self.schema.types:
            raise ClientApiError(type + ' is not a valid type')

        self._validate_list(type, **kw)
        collection_url = self.schema.types[type].links.collection
        r = yield self._get(collection_url, data=self._to_dict(**kw))
        return r

    @gen.coroutine
    def reload(self, obj):
        r = yield self.by_id(obj.type, obj.id)
        return r

    @gen.coroutine
    def create(self, type, *args, **kw):
        collection_url = self.schema.types[type].links.collection
        r = yield self._post(collection_url, data=self._to_dict(*args, **kw))
        return r

    @gen.coroutine
    def delete(self, *args):
        for i in args:
            if isinstance(i, RestObject):
                r = yield self._delete(i.links.self)
                return r

    @gen.coroutine
    def action(self, obj, action_name, *args, **kw):
        url = getattr(obj.actions, action_name)
        r = yield self._post_and_retry(url, *args, **kw)
        return r

    def _is_list(self, obj):
        if isinstance(obj, list):
            return True

        if isinstance(obj, RestObject) and 'type' in obj.__dict__ and \
                obj.type == 'collection':
            return True

        return False

    def _to_value(self, value):
        if isinstance(value, dict):
            ret = {}
            for k, v in six.iteritems(value):
                ret[k] = self._to_value(v)
            return ret

        if isinstance(value, list):
            ret = []
            for v in value:
                ret.append(self._to_value(v))
            return ret

        if isinstance(value, RestObject):
            ret = {}
            for k, v in vars(value).iteritems():
                if not k.startswith('_') and \
                        not isinstance(v, RestObject) and not callable(v):
                    ret[k] = self._to_value(v)
                elif not k.startswith('_') and isinstance(v, RestObject):
                    ret[k] = self._to_dict(v)
            return ret

        return value

    def _to_dict(self, *args, **kw):
        if len(kw) == 0 and len(args) == 1 and self._is_list(args[0]):
            ret = []
            for i in args[0]:
                ret.append(self._to_dict(i))
            return ret

        ret = {}

        for i in args:
            value = self._to_value(i)
            if isinstance(value, dict):
                for k, v in six.iteritems(value):
                    ret[k] = v

        for k, v in six.iteritems(kw):
            ret[k] = self._to_value(v)

        return ret

    @staticmethod
    def _type_name_variants(name):
        ret = [name]
        python_name = re.sub(r'([a-z])([A-Z])', r'\1_\2', name)
        if python_name != name:
            ret.append(python_name.lower())

        return ret

    def _bind_methods(self, schema):
        bindings = [
            ('list', 'collectionMethods', GET_METHOD, self.list),
            ('by_id', 'collectionMethods', GET_METHOD, self.by_id),
            ('update_by_id', 'resourceMethods', PUT_METHOD, self.update_by_id),
            ('create', 'collectionMethods', POST_METHOD, self.create)
        ]

        for type_name, typ in six.iteritems(schema.types):
            for name_variant in self._type_name_variants(type_name):
                for method_name, type_collection, test_method, m in bindings:
                    # double lambda for lexical binding hack, I'm sure there's
                    # a better way to do this
                    cb = lambda type_name=type_name, method=m: \
                        lambda *args, **kw: method(type_name, *args, **kw)
                    if test_method in getattr(typ, type_collection, []):
                        setattr(self, '_'.join([method_name, name_variant]),
                                cb())

    def _get_schema_hash(self):
        h = hashlib.new('sha1')
        h.update(self._url)
        if self._access_key is not None:
            h.update(self._access_key)
        return h.hexdigest()

    def _get_cached_schema_file_name(self):
        if not self._cache:
            return None

        h = self._get_schema_hash()

        cachedir = os.path.expanduser(CACHE_DIR)
        if not cachedir:
            return None

        if not os.path.exists(cachedir):
            os.mkdir(cachedir)

        return os.path.join(cachedir, 'schema-' + h + '.json')

    def _cache_schema(self, text):
        cached_schema = self._get_cached_schema_file_name()

        if not cached_schema:
            return None

        with open(cached_schema, 'w') as f:
            f.write(text)

    def _get_cached_schema(self):
        if not self._cache:
            return None

        cached_schema = self._get_cached_schema_file_name()

        if not cached_schema:
            return None

        if os.path.exists(cached_schema):
            mod_time = os.path.getmtime(cached_schema)
            if time.time() - mod_time < self._cache_time:
                with open(cached_schema) as f:
                    data = f.read()
                return data

        return None
