# -*- coding: utf-8 -*-
import os
from paste.urlparser import StaticURLParser
from webhelpers.paginate import Page
from sqlalchemy.orm import class_mapper, object_session
from formalchemy.fields import _pk
from formalchemy.fields import _stringify
from formalchemy import Grid, FieldSet
from formalchemy.i18n import get_translator
from formalchemy.fields import Field
from formalchemy import fatypes
from pyramid.view import view_config
from pyramid.renderers import render
from pyramid.renderers import get_renderer
from pyramid import httpexceptions as exc

try:
    from formalchemy.ext.couchdb import Document
except ImportError:
    Document = None

import simplejson as json

class Session(object):
    """A abstract class to implement other backend than SA"""
    def add(self, record):
        """add a record"""
    def update(self, record):
        """update a record"""
    def delete(self, record):
        """delete a record"""
    def commit(self):
        """commit transaction"""

class AdminView(object):
    def __init__(self, request):
        self.request = request
        request.model_name = None
        request.model_id = None
        self.__parent__ = self.__name__ = None
    def __getitem__(self, item):
        model = ModelListing(self.request, item)
        model.__parent__ = self
        return model

class ModelListing(object):
    def __init__(self, request, name):
        self.request = request
        request.model_name = name
        self.__name__ = name
        self.__parent__ = None
    def __getitem__(self, item):
        if item in ('new',):
            raise KeyError()
        model = ModelItem(self.request, item)
        model.__parent__ = self
        return model

class ModelItem(object):
    def __init__(self, request, name):
        request.model_id = name
        self.__name__ = name
        self.__parent__ = None

class ModelView(object):
    """A RESTful view bound to a model"""

    engine = prefix_name = None
    FieldSet = FieldSet
    Grid = Grid
    pager_args = dict(link_attr={'class': 'ui-pager-link ui-state-default ui-corner-all'},
                      curpage_attr={'class': 'ui-pager-curpage ui-state-highlight ui-corner-all'})

    def __init__(self, context, request):
        self.context = context
        self.request = request
        self.settings = request.registry.settings
        self.model = self.settings['fa.models']
        try:
            self.id = request.model_id
        except:
            self.id = None

    @property
    def model_name(self):
        """return ``model_name`` from ``pylons.routes_dict``"""
        try:
            return self.request.model_name
        except AttributeError:
            return None

    @property
    def template(self):
        return self.settings.get('fa.template', 'formalchemy:ext/pyramid/forms/restfieldset.pt')

    @property
    def member_name(self):
        return 'fa_admin'

    def Session(self):
        """return a Session object. You **must** override this."""
        return self.settings['fa.session_factory']()

    def models(self, format='html', **kwargs):
        """Models index page"""
        models = self.get_models()
        return self.render(models=models, format=format)

    def get_models(self):
        """return a dict containing all model names as key and url as value"""
        models = {}
        if isinstance(self.model, list):
            for model in self.model:
                key = model.__name__
                models[key] = '' #model_url(self.collection_name, model_name=key)
        else:
            for key, obj in self.model.__dict__.iteritems():
                if not key.startswith('_'):
                    if Document is not None:
                        try:
                            if issubclass(obj, Document):
                                models[key] = '' #model_url(self.collection_name, model_name=key)
                                continue
                        except:
                            pass
                    try:
                        class_mapper(obj)
                    except:
                        continue
                    if not isinstance(obj, type):
                        continue
                    models[key] = '' #model_url(self.collection_name, model_name=key)
        return models

    def get_model(self):
        if isinstance(self.model, list):
            for model in self.model:
                if model.__name__ == self.model_name:
                    return model
        elif hasattr(self.model, self.model_name):
            return getattr(self.model, self.model_name)
        raise exc.HTTPNotFound()

    def get_fieldset(self, id):
        if self.forms and hasattr(self.forms, self.model_name):
            fs = getattr(self.forms, self.model_name)
            fs.engine = fs.engine or self.engine
            return id and fs.bind(self.get(id)) or fs
        return _RESTController.get_fieldset(self, id)

    def get_add_fieldset(self):
        if self.forms and hasattr(self.forms, '%sAdd' % self.model_name):
            fs = getattr(self.forms, '%sAdd' % self.model_name)
            fs.engine = fs.engine or self.engine
            return fs
        return self.get_fieldset(id=None)

    def get_grid(self):
        model_name = self.model_name
        if self.forms and hasattr(self.forms, '%sGrid' % model_name):
            g = getattr(self.forms, '%sGrid' % model_name)
            g.engine = g.engine or self.engine
            g.readonly = True
            self.update_grid(g)
            return g
        return _RESTController.get_grid(self)

    def sync(self, fs, id=None):
        """sync a record. If ``id`` is None add a new record else save current one.

        Default is::

            S = self.Session()
            if id:
                S.merge(fs.model)
            else:
                S.add(fs.model)
            S.commit()
        """
        S = self.Session()
        if id:
            try:
                S.merge(fs.model)
            except AttributeError:
                # SA <= 0.5.6
                S.update(fs.model)
        else:
            S.add(fs.model)
        #S.commit()

    def breadcrumb(self, fs=None, **kwargs):
        """return items to build the breadcrumb"""
        items = []
        request = self.request
        model_name = request.model_name
        id = request.model_id
        items.append((request.route_url('fa_admin', traverse=""), 'root'))
        if self.model_name:
            items.append((request.route_url('fa_admin', traverse=model_name), model_name))
        if id and hasattr(fs.model, '__unicode__'):
            items.append((request.route_url('fa_admin', traverse='%s/%s' % (model_name, id)), u'%s' % fs.model))
        elif id:
            items.append((request.route_url('fa_admin', traverse='%s/%s' % (model_name, id)), id))
        return items

    def render(self, format='html', **kwargs):
        """render the form as html or json"""
        if format != 'html':
            meth = getattr(self, 'render_%s_format' % format, None)
            if meth is not None:
                return meth(**kwargs)
            else:
                return exc.HTTPNotfound()
        kwargs.update(
                      main = get_renderer('formalchemy:ext/pyramid/forms/master.pt').implementation(),
                      model_name=self.model_name,
                      breadcrumb=self.breadcrumb(**kwargs),
                      F_=get_translator().gettext)
        return kwargs

    def render_grid(self, format='html', **kwargs):
        """render the grid as html or json"""
        return self.render(format=format, is_grid=True, **kwargs)

    def render_json_format(self, fs=None, **kwargs):
        response.content_type = 'text/javascript'
        if fs:
            try:
                fields = fs.jsonify()
            except AttributeError:
                fields = dict([(field.renderer.name, field.model_value) for field in fs.render_fields.values()])
            data = dict(fields=fields)
            pk = _pk(fs.model)
            if pk:
                data['item_url'] = self.request.model_url(self.member_name, id=pk)
        else:
            data = {}
        data.update(kwargs)
        return json.dumps(data)

    def render_xhr_format(self, fs=None, **kwargs):
        response.content_type = 'text/html'
        if fs is not None:
            if 'field' in request.GET:
                field_name = request.GET.get('field')
                fields = fs.render_fields
                if field_name in fields:
                    field = fields[field_name]
                    return field.render()
                else:
                    return exc.HTTPNotfound()
            return fs.render()
        return ''

    def get_page(self, **kwargs):
        """return a ``webhelpers.paginate.Page`` used to display ``Grid``.

        Default is::

            S = self.Session()
            query = S.query(self.get_model())
            kwargs = request.environ.get('pylons.routes_dict', {})
            return Page(query, page=int(request.GET.get('page', '1')), **kwargs)
        """
        S = self.Session()
        options = dict(collection=S.query(self.get_model()), page=int(self.request.GET.get('page', '1')))
        options.update(kwargs)
        collection = options.pop('collection')
        return Page(collection, **options)

    def get(self, id=None):
        """return correct record for ``id`` or a new instance.

        Default is::

            S = self.Session()
            model = self.get_model()
            if id:
                model = S.query(model).get(id)
            else:
                model = model()
            return model or abort(404)

        """
        S = self.Session()
        model = self.get_model()
        if id:
            model = S.query(model).get(id)
        if model:
            return model
        raise exc.HTTPNotFound()

    def get_fieldset(self, id=None):
        """return a ``FieldSet`` object bound to the correct record for ``id``.

        Default is::

            fs = self.FieldSet(self.get(id))
            fs.engine = fs.engine or self.engine
            return fs
        """
        fs = self.FieldSet(self.get(id))
        fs.engine = fs.engine or self.engine
        return fs

    def get_add_fieldset(self):
        """return a ``FieldSet`` used for add form.

        Default is::

            fs = self.get_fieldset()
            for field in fs.render_fields.itervalues():
                if field.is_readonly():
                    del fs[field.name]
            return fs
        """
        fs = self.get_fieldset()
        for field in fs.render_fields.itervalues():
            if field.is_readonly():
                del fs[field.name]
        return fs

    def get_grid(self):
        """return a Grid object

        Default is::

            grid = self.Grid(self.get_model())
            grid.engine = self.engine
            self.update_grid(grid)
            return grid
        """
        grid = self.Grid(self.get_model())
        grid.engine = self.engine
        self.update_grid(grid)
        return grid


    def update_grid(self, grid):
        """Add edit and delete buttons to ``Grid``"""
        try:
            grid.edit
        except AttributeError:
            def edit_link():
                return lambda item: '''
                <form action="%(url)s" method="GET" class="ui-grid-icon ui-widget-header ui-corner-all">
                <input type="submit" class="ui-grid-icon ui-icon ui-icon-pencil" title="%(label)s" value="%(label)s" />
                </form>
                ''' % dict(url=self.request.route_url('fa_admin', traverse='%s/%s/edit' % (self.request.model_name, _pk(item))),
                            label=get_translator().gettext('edit'))
            def delete_link():
                return lambda item: '''
                <form action="%(url)s" method="POST" class="ui-grid-icon ui-state-error ui-corner-all">
                <input type="submit" class="ui-icon ui-icon-circle-close" title="%(label)s" value="%(label)s" />
                </form>
                ''' % dict(url=self.request.route_url('fa_admin', traverse='%s/%s/delete' % (self.request.model_name, _pk(item))),
                           label=get_translator().gettext('delete'))
            grid.append(Field('edit', fatypes.String, edit_link()))
            grid.append(Field('delete', fatypes.String, delete_link()))
            grid.readonly = True

    def listing(self, format='html', **kwargs):
        """listing page"""
        page = self.get_page()
        fs = self.get_grid()
        fs = fs.bind(instances=page)
        fs.readonly = True
        if format == 'json':
            values = []
            request = self.request
            for item in page:
                pk = _pk(item)
                fs._set_active(item)
                value = dict(id=pk,
                             item_url=request.route_url('fa_admin', traverse='%s/%s' % (request.model_name, pk)))
                if 'jqgrid' in request.GET:
                    fields = [_stringify(field.render_readonly()) for field in fs.render_fields.values()]
                    value['cell'] = [pk] + fields
                else:
                    value.update(dict([(field.key, field.model_value) for field in fs.render_fields.values()]))
                values.append(value)
            return self.render_json_format(rows=values,
                                           records=len(values),
                                           total=page.page_count,
                                           page=page.page)
        if 'pager' not in kwargs:
            pager = page.pager(**self.pager_args)
        else:
            pager = kwargs.pop('pager')
        return self.render_grid(format=format, fs=fs, id=None, pager=pager)

    def create(self):
        """REST api"""
        format = 'html'
        request = self.request
        S = self.Session()
        fs = self.get_add_fieldset()

        if format == 'json' and request.method == 'PUT':
            data = json.load(request.body_file)
        else:
            data = request.POST

        try:
            fs = fs.bind(data=data, session=S)
        except:
            # non SA forms
            fs = fs.bind(self.get_model(), data=data, session=S)
        if fs.validate():
            fs.sync()
            self.sync(fs)
            S.flush()
            if format == 'html':
                if request.is_xhr:
                    response.content_type = 'text/plain'
                    return ''
                return exc.HTTPFound(
                    location=request.route_url('fa_admin', traverse='%s' % (request.model_name,)))
            else:
                fs.rebind(fs.model, data=None)
                return self.render(format=format, fs=fs)
        return self.render(format=format, fs=fs, action='new', id=None)

    def delete(self, format='html', **kwargs):
        """REST api"""
        request = self.request
        id = request.model_id
        record = self.get(id)
        if record:
            S = self.Session()
            S.delete(record)
            #S.commit()
        if format == 'html':
            if self.request.is_xhr:
                response = Response()
                response.content_type = 'text/plain'
                return response
            return exc.HTTPFound(location=request.route_url('fa_admin', traverse=request.model_name))
        return self.render(format=format, id=id)

    def show(self):
        """REST api"""
        fs = self.get_fieldset(id=self.id)
        fs.readonly = True
        return self.render(fs=fs, action='show', id=id)

    def new(self, format='html', **kwargs):
        """REST api"""
        fs = self.get_add_fieldset()
        fs = fs.bind(session=self.Session())
        return self.render(format=format, fs=fs, action='new', id=None)

    def edit(self, id=None, format='html', **kwargs):
        """REST api"""
        id = self.request.model_id
        fs = self.get_fieldset(id)
        return self.render(format=format, fs=fs, action='edit', id=id)

    def update(self, format='html', **kwargs):
        """REST api"""
        request = self.request
        S = self.Session()
        id = self.request.model_id
        fs = self.get_fieldset(id)
        if format == 'json' and request.method == 'PUT' and '_method' not in request.GET:
            data = json.load(request.body_file)
        else:
            data = request.POST
        fs = fs.bind(data=data)
        if fs.validate():
            fs.sync()
            self.sync(fs, id)
            S.flush()
            if format == 'html':
                if request.is_xhr:
                    response.content_type = 'text/plain'
                    return ''
                return exc.HTTPFound(
                        location=self.request.route_url(
                                    'fa_admin',
                                    traverse='%s/%s' % (request.model_name, _pk(fs.model))))
            else:
                return self.render(format=format, fs=fs, status=0)
        if format == 'html':
            return self.render(format=format, fs=fs, action='edit', id=id)
        else:
            return self.render(format=format, fs=fs, status=1)
