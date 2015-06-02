from flask import request, jsonify, Blueprint, Response, flash
from Queue import Empty

import flexget
from flexget.config_schema import resolve_ref, process_config, get_schema
from flexget.manager import manager
from flexget.plugin import plugin_schemas
from flexget.utils.tools import BufferQueue
from flexget.options import get_parser

API_VERSION = 1

api = Blueprint('api', __name__, url_prefix='/api')

# Serves the appropriate schema for any /api method. Schema for /api/x/y can be found at /schema/api/x/y
api_schema = Blueprint('api_schema', __name__, url_prefix='/schema/api')


@api.after_request
def attach_schema(response):
    # TODO: Check if /schema/ourpath exists
    schema_path = '/schema' + request.path
    response.headers[b'Content-Type'] += '; profile=%s' % schema_path
    return response


@api.route('/version/')
def version():
    return jsonify(flexget_version=flexget.__version__, api_version=API_VERSION)


# Execution API
@api.route('/execution/', methods=['GET', 'POST'])
def execution():
    if request.method == 'GET':
        return jsonify({"tasks": [task_state for task_state in manager.task_queue.tasks_data.itervalues()]})

    if request.method == "POST":
        kwargs = request.json or {}

        options_string = kwargs.pop('options_string', '')
        if options_string:
            try:
                kwargs['options'] = get_parser('execute').parse_args(options_string, raise_errors=True)
            except ValueError as e:
                return jsonify(error='invalid options_string specified: %s' % e.message), 400

        tasks = manager.execute(**kwargs)

        return jsonify({"tasks": [manager.task_queue.tasks_data.get(task_id) for task_id, event in tasks]})


@api.route('/execution/<task_id>/')
def execution_by_id(task_id):
    task_info = manager.task_queue.tasks_data.get(task_id)

    if not task_info:
        return jsonify({'detail': '%s not found' % task_id}), 400

    return jsonify({'task': task_info})


@api.route('/execution/<task_id>/log/')
def execution_log(task_id):
    task_info = manager.task_queue.tasks_data.get(task_id)

    if not task_info:
        return jsonify({'detail': '%s not found' % task_id}), 400

    def read_log():
        while task_info['status'] == "running":
            # TODO: get log file data..
            pass

    return Response(read_log(), mimetype='text/event-stream')


# Task API
# TODO: Maybe these should be in /config/tasks
@api_schema.route('/tasks/')
def schema_tasks():
    return jsonify({
        'type': 'object',
        'properties': {
            'tasks': {
                'type': 'array',
                'items': {'$ref': '/schema/api/tasks/task'},
                'links': [
                    {'rel': 'add', 'method': 'POST', 'href': '/api/tasks/', 'schema': {'$ref': '/schema/api/tasks/task'}}
                ]
            }
        }
    })


@api.route('/tasks/', methods=['GET', 'POST'])
def api_tasks():
    if request.method == 'GET':
        tasks = []
        for name in manager.tasks:
            tasks.append({'name': name, 'config': manager.config['tasks'][name]})
        return jsonify(tasks=tasks)
    elif request.method == 'POST':
        # TODO: Validate and add task
        pass


@api_schema.route('/tasks/<task>/')
def schema_task(task):
    return jsonify({
        'type': 'object',
        'properties': {
            'name': {'type': 'string', 'description': 'The name of this task.'},
            'config': plugin_schemas(context='task')
        },
        'required': ['name'],
        'additionalProperties': False,
        'links': [
            {'rel': 'self', 'href': '/api/tasks/{name}/'},
            {'rel': 'edit', 'method': 'PUT', 'href': '', 'schema': {'$ref': '#'}},
            {'rel': 'delete', 'method': 'DELETE', 'href': ''}
        ]
    })


@api.route('/tasks/<task>/', methods=['GET', 'PUT', 'DELETE'])
def api_task(task):
    if request.method == 'GET':
        if not task in manager.tasks:
            return jsonify(error='task {task} not found'.format(task=task)), 404
        return jsonify({'name': task, 'config': manager.config[task]})
    elif request.method == 'PUT':
        # TODO: Validate then set
        # TODO: Return 204 if name has been changed
        pass
    elif request.method == 'DELETE':
        manager.config['tasks'].pop(task)


@api_schema.route('/config/')
def cs_root():
    root_schema = get_schema()
    hyper_schema = root_schema.copy()
    hyper_schema['links'] = [{'rel': 'self', 'href': '/api/config/'}]
    hyper_schema['properties'] = root_schema.get('properties', {}).copy()
    hs_props = hyper_schema['properties']
    for key, key_schema in root_schema.get('properties', {}).iteritems():
        hs_props[key] = hs_props[key].copy()
        hs_props[key]['links'] = [{'rel': 'self', 'href': key}]
        if key not in root_schema.get('required', []):
            hs_props[key]['links'].append({'rel': 'delete', 'href': '', 'method': 'DELETE'})
    return jsonify(hyper_schema)


# TODO: none of these should allow setting invalid config
@api.route('/config/', methods=['GET', 'PUT'])
def config_root():
    return jsonify(manager.config)


@api_schema.route('/config/<section>')
def schema_config_section(section):
    return jsonify(resolve_ref('/schema/config/%s' % section))


@api.route('/config/<section>/', methods=['GET', 'PUT', 'DELETE'])
def config_section(section):
    if request.method == 'PUT':
        schema = resolve_ref('/schema/config/%s' % section)
        errors = process_config(request.json, schema, set_defaults=False)
        if errors:
            return jsonify({'$errors': errors}), 400
        manager.config[section] = request.json
    if section not in manager.config:
        return jsonify(error='Not found'), 404
    if request.method == 'DELETE':
        del manager.config[section]
        return Response(status=204)
    response = jsonify(manager.config[section])
    response.headers[b'Content-Type'] += '; profile=/schema/config/%s' % section
    return response


# TODO: Abandon this and move above task handlers into /config?
@api.route('/config/tasks/<taskname>/', methods=['GET', 'PUT', 'DELETE'])
def config_tasks(taskname):
    if request.method != 'PUT':
        if taskname not in manager.config['tasks']:
            return jsonify(error='Requested task does not exist'), 404
    status_code = 200
    if request.method == 'PUT':
        if 'rename' in request.args:
            pass  # TODO: Rename the task, return 204 with new location header
        if taskname not in manager.config['tasks']:
            status_code = 201
        manager.config['tasks'][taskname] = request.json
    elif request.method == 'DELETE':
        del manager.config['tasks'][taskname]
        return Response(status=204)
    return jsonify(manager.config['tasks'][taskname]), status_code


# TODO: Move this route to template plugin
@api_schema.route('/config/templates/', defaults={'section': 'templates'})
@api_schema.route('/config/tasks/', defaults={'section': 'tasks'})
def cs_task_container(section):
    hyper_schema = {'links': [{'rel': 'create',
                               'href': '',
                               'method': 'POST',
                               'schema': {
                                   'type': 'object',
                                   'properties': {'name': {'type': 'string'}},
                                   'required': ['name']}}]}


# TODO: Move this route to template plugin
@api_schema.route('/config/templates/<name>', defaults={'section': 'templates'})
@api_schema.route('/config/tasks/<name>', defaults={'section': 'tasks'})
def cs_plugin_container(section, name):
    return plugin_schemas(context='task')
