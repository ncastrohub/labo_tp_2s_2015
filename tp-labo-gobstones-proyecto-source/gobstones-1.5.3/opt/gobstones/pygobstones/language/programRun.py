from PyQt4 import QtCore
import pygobstones.commons.messaging as messaging
import Queue as queue
import pygobstones.commons.concurrent as concurrent
import pygobstoneslang
from pygobstoneslang import ProgramWorker

debug = False


def reverse_list(lst):
    return [lst.pop() for _ in range(len(lst))]


class EjecutionHandler(object):

    def success(self, board_string, result):
        pass

    def failure(self, exception):
        pass

    def log(self, message_string):
        pass

    def read_request(self):
        pass

    def partial(self, board_string):
        pass


class EjecutionFailureHandler(object):
    """ Serves exception based on an internal dictionary which
    specifies different failure handlers for each failure type.
    """

    """ Failure type constants """
    DEFAULT = 'GobstonesException'
    PARSER_FAILURE = 'GbsParserException|ParserException'
    SEMANTIC_FAILURE = 'GbsLintException'
    LIVENESS_FAILURE = 'GbsLivenessException|GbsUninitializedVarException|GbsUnusedVarException'
    TYPECHECK_FAILURE = 'GbsTypeInferenceException'
    TYPESYNTAX_FAILURE = 'GbsTypeSyntaxException'
    OPTIMIZER_FAILURE = 'GbsOptimizerException'
    COMPILER_FAILURE = 'GbsCompileException'
    RUNTIME_FAILURE = 'GbsRuntimeException'
    RUNTIMETYPE_FAILURE = 'GbsRuntimeTypeException'
    VM_FAILURE = 'GbsVmException'
    STATIC_FAILURE = '|'.join([PARSER_FAILURE,
                               SEMANTIC_FAILURE,
                               LIVENESS_FAILURE,
                               TYPECHECK_FAILURE,
                               TYPESYNTAX_FAILURE,
                               'StaticException'])
    DYNAMIC_FAILURE = '|'.join([RUNTIME_FAILURE,
                                RUNTIMETYPE_FAILURE,
                                VM_FAILURE,
                                OPTIMIZER_FAILURE,
                                'DynamicException'])

    def __init__(self, handler_or_dict):
        """ Receives a failure handler or a dictionary of handlers.
        Dicionary keys are expected to be one of the constants defined in this class.
        """
        if isinstance(handler_or_dict, dict):
            self.exception_handlers = handler_or_dict
        else:
            self.exception_handlers = {self.DEFAULT: handler_or_dict}

    def failure(self, exception):
        """ Exception will be served only once. Singular failure types have
        the top priority. If there is no handler for the exception, general failure
        handlers {STATIC | DYNAMIC} are dispatched to serve the exception. If it still
        remain unserved, DEFAULT handler is dispatched.
        """
        exception_type = exception.__class__.__name__
        handlers = self.get_handlers_for(exception_type)

        if len(handlers) != 0:
            handlers[0](exception)
        else:
            raise Exception('There is no failure handler defined for %s.' % (exception_type,))

    def get_handlers_for(self, exception_type):
        return self.sort_handlers([(failure_type, handler) for failure_type, handler in self.exception_handlers.items() if self.is_failure_type(exception_type, failure_type)])

    def sort_handlers(self, handlers):
        dict_handlers = dict(handlers)
        sorted_list = [dict_handlers.pop(self.DEFAULT, None),
                       dict_handlers.pop(self.DYNAMIC_FAILURE, None),
                       dict_handlers.pop(self.STATIC_FAILURE, None)]
        sorted_list.extend(dict_handlers.values())
        return reverse_list(filter(None, sorted_list))

    def is_failure_type(self, exception_type, failure_type):
        return exception_type in failure_type.split('|')

    def is_handler_defined(self, failure_type):
        return self.PARSER_FAILURE in self.exception_handlers.keys()


class ProgramRun(object):
    RunMode = ProgramWorker.RunMode

    def __init__(self, gobstones_version, handler=EjecutionHandler()):
        self.running = False
        self.process = None
        self.worker = None
        self.handler = handler
        self.comm = None
        self.gobstones_version = gobstones_version
        self.gbs_language = pygobstoneslang

    def get_worker_class(self):
        return self.gbs_language.GobstonesWorker

    def create_worker_process(self):
        if self.process is None:
            self.comm = messaging.MessageCommunicator()
            self.worker = self.get_worker_class()(self.comm.opposite())

            if debug:
                self.process = concurrent.Thread(target=self.worker.run)
            else:
                self.process = concurrent.Process(target=self.worker.run)
            self.process.start()

    def destroy_worker_process(self):
        if not self.process is None:
            if hasattr(self.process, 'terminate'):
                self.process.terminate()
            self.process = None

    def timer_init(self):
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.continue_run)
        self.timer.start(1000)

    def run(self, filename, current_text, board_string, run_mode=RunMode.FULL):
        if not self.process is None:
            self.destroy_worker_process()
        self.create_worker_process()
        
        self.comm.send('START', (
            filename,
            current_text.encode('ascii', 'ignore'),
            board_string,
            run_mode,
            self.gobstones_version
            ))
        self.running = True
        self.timer_init()

    def send_input(self, keycode):
        if self.running:
            self.comm.send('READ_DONE', keycode)

    def continue_run(self):
        if not self.running:
            return
        self.timer.stop()
        try:
            while True:
                message = self.comm.receive_nowait()

                if message.header == 'OK':
                    self.handler.success(*message.body)
                    self.stop()
                elif message.header == 'FAIL':
                    reduced = message.body
                    args = list(reduced[1])
                    self.handler.failure(reduced[0](*args))
                    self.stop()
                elif message.header == 'READ_REQUEST':
                    self.handler.read_request()
                elif message.header == 'LOG':
                    self.handler.log(message.body)
                elif message.header == 'PARTIAL':
                    self.handler.partial(message.body)
                else:
                    print("GUI got an unexpected message '%s:%s'" %  (message.header, message.body))
        except queue.Empty as e:
            self.timer.start()

    def stop(self):
        self.timer.stop()
        self.destroy_worker_process()
        self.running = False

    def abort(self):
        self.destroy_worker_process()
        self.running = False
        self.handler.failure(Exception('Execution interrupted by the user'))
