#!/usr/bin/python
#
# Copyright (C) 2011-2013 Pablo Barenbaum <foones@gmail.com>,
#                         Ary Pablo Batista <arypbatista@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
from __future__ import absolute_import

import os
import sys
import pygobstoneslang.common.i18n as i18n
import pygobstoneslang.common.utils as utils
import pygobstoneslang.common.logtools as logtools
import pygobstoneslang.lang as lang
import pygobstoneslang.lang.board as board
from pygobstoneslang.lang.gbs_io import get_key_set
import logging
import json
from pygobstoneslang.common.utils import SourceException, GobstonesException
from pygobstoneslang.lang.gbs_vm import NullInteractiveAPI

LOGGER = logtools.get_logger('gbs-console')
LICENSE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "LICENSE.txt")


def report_error(errtype, msg):
    LOGGER.error('%s:\n' % (errtype,))
    LOGGER.error('%s\n' % (utils.indent(msg),))


def report_program_error(errtype, msg, area):
    LOGGER.error('\n%s\n' % (area,))
    report_error(errtype, msg)


def get_initial_board(options):
    if options['from']:
        format_ = options['from'].split('.')[-1].lower()
        if format_ not in board.formats.AvailableFormats:
            format_ = board.formats.DefaultFormat
        board_file = open(options['from'], 'r')
        initial_board = lang.Board()
        initial_board.load(board_file, fmt=format_)
        board_file.close()
    else:
        initial_board = lang.Gobstones.random_board(options['size'])
    return initial_board


def usage(ret=1):
    msg = i18n.i18n('I18N_gbs_usage')
    msg = msg.replace('<PROG>', sys.argv[0])
    LOGGER.error(msg)
    sys.exit(ret)


class OptionsException(Exception):

    def __init__(self, msg=''):
        super(OptionsException, self).__init__()
        self.msg = msg

    def error_type(self):
        return 'Error'


class GbsOptions(object):
    SWITCHES = [
        '--from X',
        '--to X',
        '--size X X',
        '--print-input',
        '--no-print-board',
        '--no-print-retvals',
        '--print-ast',
        '--print-asm',
        '--pprint',
        '--no-typecheck',
        '--no-liveness',
        '--lint X',
        '--asm X',
        '--src X',
        '--style X',
        '--version',
        '--license',
        '--target X',
        '--help',
        '--profile',
        '--silent',
        '--interactive',
        '--output-type X',
        '--language X',
        '--recursion',
        '--names',
        '--keyset'
    ]

    def __init__(self, argv):
        if len(argv) == 1:
            argv.append('--help')
        opts = utils.parse_options(GbsOptions.SWITCHES, argv)
        if not opts:
            raise OptionsException()
        self.arguments = opts[0]
        self.options = opts[1]
        self.options = self.build(self.options)

    def __getitem__(self, i):
        return self.options[i]

    def merge(self, opts1, opts2):
        opts_merge = {}
        keys = set(opts1.keys() + opts2.keys())
        for k in keys:
            opts_merge[k] = opts1[k] or opts2[k]
        return opts_merge

    def maybe(self, options, key, else_=None):
        if not key in options.keys() or options[key] == [] or options[key] is None:
            return else_
        else:
            return options[key][0]

    def build(self, options):
        if len(self.arguments) not in [0, 1, 2] and not options['src']:
            raise OptionsException()

        if len(self.arguments) == 2:
            if options['from']:
                raise OptionsException()
            else:
                options['from'] = self.arguments[1]

        if len(self.arguments) >= 1:
            if options['src']:
                raise OptionsException()
            else:
                options['src'] = self.arguments[0]
        for k,v in options.items():
            if k == 'lint':
                options[k] = self.maybe(options, k, 'strict')
            elif k == 'style':
                options[k] = self.maybe(options, k, 'verbose')
            elif k == 'target':
                options[k] = self.maybe(options, k, 'run')
            elif k == 'language':
                options[k] = self.maybe(options, k, 'xgobstones')
            elif isinstance(v, list) and len(v) <= 1:
                options[k] = self.maybe(options, k)

        self.check(options)
        if options['size']:
            options['size'] = [int(x) for x in options['size']]

        return options

    def check(self, options):
        if options['src']:
            self.check_file_exists(options['src'])
        if options['from']:
            self.check_file_exists(options['from'])
        if options['lint'] not in lang.GobstonesOptions.LINT_MODES:
            raise OptionsException(i18n.i18n('%s is not a valid lint option.') % (options['lint'],))
        if not self.check_size(options['size']):
            raise OptionsException(i18n.i18n('Size %s is not a valid size. Positive integers expected.') % (str(options['size']),))

    def check_size(self, size):
        if size:
            width, height = size
            if not utils.is_int(width) or not utils.is_int(height):
                return False
            width = int(width)
            height = int(height)
            if width < 1 or height < 1:
                return False
        return True

    def check_file_exists(self, filename):
        if not os.path.exists(filename):
            raise OptionsException(i18n.i18n('File %s does not exist') % (filename,))


def print_run(gbs_run, options):
    if options['print-ast'] and options['output-type'] == 'json':
        LOGGER.info(gbs_run.tree.json())
    elif options['output-type'] == 'json':
        LOGGER.info(gbs_run.json())
    else:
        if options['print-ast']:
            LOGGER.info(gbs_run.tree.show_ast(show_liveness=False))
        if options['pprint']:
            LOGGER.info(lang.pretty_print(gbs_run.tree))
        if options['print-asm']:
            LOGGER.info(gbs_run.compiled_program)
        if not options['asm']:
            if options['print-input']:
                LOGGER.info(gbs_run.initial_board)
            if options['print-board'] and not gbs_run.final_board is None:
                LOGGER.info(gbs_run.final_board)
            if options['print-retvals']:
                for var, val in gbs_run.result:
                    LOGGER.info('%s -> %s' % (var, val))
                if not options['print-board']:
                    LOGGER.info('OK')


def persist_run(gbs_run, options):
    if options['asm']:
        f = open(options['asm'], 'w')
        w = lang.gbs_vm_serializer.dump(gbs_run.compiled_program, f, style=options['style'])
        f.write(w)
        f.close()

    if options['to'] and gbs_run.final_board:
        f = open(options['to'], 'w')
        fmt = options['to'].split('.')[-1]
        fmt = fmt.lower()
        if fmt not in board.formats.AvailableFormats:
            fmt = board.formats.DefaultFormat
        gbs_run.final_board.dump(f, fmt=fmt, style=options['style'])
        f.close()


class ConsoleInteractiveAPI(lang.ExecutionAPI):

    def __init__(self, options):
        self.options = options

    def read(self):
        char = utils.getch()
        return ord(char)

    def show(self, partial_board):
        utils.clear()
        self.log(partial_board)

    def log(self, msg):
        if not self.options['silent']:
            LOGGER.info(msg)


class ConsoleNullInteractiveAPI(NullInteractiveAPI):

    def __init__(self, options):
        super(ConsoleNullInteractiveAPI, self).__init__()
        self.options = options

    def log(self, msg):
        if not self.options['silent']:
            LOGGER.info(msg)


def get_lang(options):
    if options["language"] == 'xgobstones':
        lang_version = lang.GobstonesOptions.LangVersion.XGobstones
    else:
        lang_version = lang.GobstonesOptions.LangVersion.Gobstones
    return lang_version


def run_filename(filename, options):

    if not options["language"] in ['gobstones', 'xgobstones']:
        report_error(
            "Options Error",
            "Language %s is not supported by this interpreter." % (
                options["language"],
                )
            )
        usage(2)

    lang_version = get_lang(options)

    gbs_opts = lang.GobstonesOptions(
        lang_version,
        options['lint'],
        options['liveness'],
        options['typecheck'],
        allow_recursion=options["recursion"]
        )

    if options['interactive']:
        api = ConsoleInteractiveAPI(options)
    else:
        api = ConsoleNullInteractiveAPI(options)
    gobstones = lang.Gobstones(gbs_opts, api)

    if options['target'] == 'parse':
        gbs_run = gobstones.parse(filename, open(filename).read())
    elif options['target'] == 'check':
        gbs_run = gobstones.parse(filename, open(filename).read())
        gobstones.check(gbs_run.tree)
        LOGGER.info(i18n.i18n("Program check was successful."))
    elif filename.lower().endswith('.gbo'):
        object_file = open(filename)
        compiled_program = lang.gbs_vm_serializer.load(object_file)
        object_file.close()
        gbs_run = gobstones.run_object_code(compiled_program, get_initial_board(options))
    elif options['asm']:
        gbs_run = gobstones.compile(filename, open(filename).read())
    elif board.formats.is_board_filename(filename):
        gbs_run = lang.GobstonesRun()
        gbs_run.final_board = lang.gbs_board.load_board_from(filename)
    else:
        gbs_run = gobstones.run(filename, open(filename).read(), get_initial_board(options))
    return gbs_run

def main():
    try:
        options = GbsOptions(sys.argv)

    except OptionsException as exception:
        if exception.msg != '':
            report_error(exception.error_type(), exception.msg)
            sys.exit(1)
        else:
            usage()
    if options['version']:
        LOGGER.info(utils.version_number())
        sys.exit(0)
    elif options['help']:
        usage(0)
    elif options['license']:
        LOGGER.info(utils.read_file(LICENSE_FILE))
        sys.exit(0)
    elif options['keyset']:
        key_set = ["%s (%s)" % kv for kv in get_key_set()]
        LOGGER.info(i18n.i18n("Interactive program key set: %s") % (
                       "\n\t" + "\n\t".join(key_set),))
    elif options['src']:
        if options['names']:
            gobstones = lang.Gobstones(lang.GobstonesOptions(get_lang(options)))
            print json.dumps(gobstones.parse_names(options['src'], open(options['src']).read()))
        else:
            try:
                gbs_run = run_filename(options['src'], options)
            except SourceException as exception:
                report_program_error(exception.error_type(), exception.msg, exception.area)
                sys.exit(1)
            except GobstonesException as exception:
                report_error(i18n.i18n("%s Error") % ("Gobstones",), exception.msg)
                sys.exit(4)
            except Exception as exception:
                report_error(i18n.i18n("%s Error") % ("Python",), "Failed to execute %s file." % (options['src'],))
                logging.exception(str(exception))
                sys.exit(3)

            print_run(gbs_run, options)
            persist_run(gbs_run, options)
    else:
        usage()

if __name__ == '__main__':
    main()
