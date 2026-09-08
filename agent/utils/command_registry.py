# command_registry.py — shared handles that commands operate on
_registry = {}
_status = "active"
_static_collectors = []
_running_threads = {}
_paused_threads = {}


def add_static_collector(collector):
    _static_collectors.append(collector)


def register(name, obj):
    _registry[name] = obj

def get_handler(name):
    return _registry.get(name)

def get_status():
    return _status

def set_status(st):
    print(f"upadting status to {st}")
    if st == "pause":
        for collector in _static_collectors:
            collector.stop()

        for k , v in _running_threads.items():
            handler = v.get("handler")
            args = v.get("args")
            handler.stop(args)


    elif st == "active":
        for collector in _static_collectors:
                collector.start()

        for k , v in _registry.items():
            handler = v.get("handler")
            args = v.get("args")
            handler.start(args)

    _status = st
    return _status




def register_thread(service_name ,handler ,  args):
    _running_threads[service_name] = {"handler" : handler , "args" : args}
    return handler.start(args)


def pause_thread(service_name):
    th = _running_threads.get(service_name)
    if th:
        handler = _running_threads[service_name].get("handler")
        args = _running_threads[service_name].get("args")
        _paused_threads[service_name] = {"handler" : handler , "args" : args}
        return handler.stop(args)


def restart_thread(service_name):
    th = _paused_threads.get(service_name)
    if th:
        handler = _running_threads[service_name].get("handler")
        args = _running_threads[service_name].get("args")
        _running_threads[service_name] = {"handler" : handler , "args" : args}
        _paused_threads.remove(service_name)
        return handler.start(args)


def remove_thread(service_name):
    th = _running_threads.get(service_name)
    if th:
        handler = _running_threads[service_name].get("handler")
        args = _running_threads[service_name].get("args")
        _running_threads.remove(service_name)
        return handler.stop(args)

    else:
        th = _paused_threads.get(service_name)
        if th:
            _paused_threads.remove(service_name)
            return {"success" : True}