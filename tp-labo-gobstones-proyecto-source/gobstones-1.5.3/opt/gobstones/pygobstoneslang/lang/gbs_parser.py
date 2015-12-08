#
# Copyright (C) 2011, 2012 Pablo Barenbaum <foones@gmail.com>
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

import os
import re

import pygobstoneslang.common.position as position
import bnf_parser
import ast
import gbs_builtins
import pygobstoneslang.common.i18n as i18n
from pygobstoneslang.common.utils import *
import grammar.i18n
from gbs_api import GobstonesOptions
from grammar import XGbsGrammarFile

#### Parser of Gobstones programs.
####
#### Complements gbs_grammar.bnf solving conflicts and
#### checking for errors.

class GbsParserException(bnf_parser.ParserException):
    pass

TOKEN_BUFFER_SIZE = 4

class GbsLexer(bnf_parser.Lexer):
    """Lexical analyzer that identifies common lexical errors in Gobstones
       source files and solves the conflictive situation in the LL(1) grammar
       by inserting an extra semicolon (;) in the appropiate case."""

    def __init__(self, tokens, reserved, *args, **kwargs):
        bnf_parser.Lexer.__init__(self, tokens, reserved, *args, **kwargs)
        self.reserved_lower = {}
        for x in reserved:
            self.reserved_lower[x.lower()] = x
        for x in gbs_builtins.get_correct_names():
            self.reserved_lower[x.lower()] = x

    def _tokenize_solve_conflict(self, string, filename):
        self.supertok = bnf_parser.Lexer.tokenize(self, string, filename)
        self.token_queue = []
        q = []
        try:
            self.token_mem = [self._read()]
            yield self.token_mem[0]
            while True:
                curr_tok = self._read()
                self.token_mem.append(curr_tok)
                if self._conflictive_case():
                    # scan input, balancing parentheses
                    op = 1
                    q = [curr_tok]
                    while op > 0:
                        tok = self._read()
                        q.append(tok)
                        if tok.type == '(':
                            op += 1
                        elif tok.type == ')':
                            op -= 1
                    tok = self._read()
                    q.append(tok)
                    if tok.type == ':=': # insert a semicolon
                        yield bnf_parser.Token(';', ';', curr_tok.pos_begin, curr_tok.pos_end)
                    self.token_queue = q + self.token_queue
                    q = []
                else:
                    yield curr_tok
                if len(self.token_mem) > TOKEN_BUFFER_SIZE:
                    self.token_mem.pop(0)
        except StopIteration:
            for tok in q: yield tok

    def _read(self):
        if self.token_queue == []:
            return next(self.supertok)
        else:
            return self.token_queue.pop(0)

    def _conflictive_case(self):
        return len(self.token_mem) >= 3 and \
                      self.token_mem[-3].type == ':=' and \
                      self.token_mem[-2].type == 'lowerid' and \
                      self.token_mem[-1].type == '('

    def tokenize(self, string, filename='...'):
        supertok = self._tokenize_solve_conflict(string, filename)
        previous_token = next(supertok)
        yield previous_token
        open_parens = []
        for tok in supertok:
            area = position.ProgramAreaNear(tok)
            if tok.type in ['upperid', 'lowerid']:
                self.warn_if_similar_to_reserved(tok.value, area)

            if tok.type == 'ERROR':
                msg = i18n.i18n('Malformed input - unrecognized symbol')
                raise GbsParserException(msg, position.ProgramAreaNear(tok))
            elif tok.type == 'string_start':
                msg = i18n.i18n('Unterminated string')
                raise GbsParserException(msg, position.ProgramAreaNear(tok))

            """ [TODO] Replace this checking by similar check for 'procedure lowerid.upperid' """
            if False and previous_token.type in ['procedure'] and tok.type not in ['upperid']:
                l1 = i18n.i18n('Found: %s') % tok
                l2 = i18n.i18n('procedure name should be an uppercase identifier')
                raise GbsParserException('\n'.join([l1, l2]), area)
            elif previous_token.type in ['function'] and tok.type not in ['lowerid']:
                l1 = i18n.i18n('Found: %s') % tok
                l2 = i18n.i18n('function name should be a lowercase identifier')
                raise GbsParserException('\n'.join([l1, l2]), area)
            elif tok.type in [',', ';', 'not', 'num', 'string'] and previous_token.type == tok.type:
                raise GbsParserException(i18n.i18n('Repeated symbol: %s') % (tok,), area)
            elif (previous_token.type, tok.type) in [(',', ')'),
                                                     (';', ')'),
                                                     ('(', ','),
                                                    ]:
                msg = i18n.i18n('%s cannot be followed by %s') % (previous_token, tok,)
                raise GbsParserException(msg, area)
            elif previous_token.type == 'return' and tok.type != '(':
                raise GbsParserException(i18n.i18n('return must be followed by "("'), area)
            elif len(self.token_mem) >= 3 and self.token_mem[-3].type == 'THROW_ERROR' and self.token_mem[-1].type != 'string':
                raise GbsParserException(i18n.i18n('THROW_ERROR can only accept a string'), area)

            # check opening/closing parens and braces
            if tok.type in ['(', '{']:
                open_parens.append(tok)
            elif tok.type in [')', '}']:
                # check if there is an opening token
                if len(open_parens) == 0:
                    if tok.type == ')':
                        msg = 'Found closing ")" with no matching open paren'
                    else:
                        msg = 'Found closing "}" with no matching open brace'
                    raise GbsParserException(i18n.i18n(msg), area)
                # check if the opening token is of the right kind
                opening = open_parens.pop()
                if opening.type == '(' and tok.type != ')':
                    open_area = position.ProgramAreaNear(opening)
                    l1 = i18n.i18n('Found open "(" with no matching closing paren')
                    l2 = i18n.i18n('Maybe there is an extra "%s" at %s') % (
								tok.type, tok.pos_begin.row_col(),)
                    raise GbsParserException(i18n.i18n('\n'.join([l1, l2])), open_area)
                elif opening.type == '{' and tok.type != '}':
                    open_area = position.ProgramAreaNear(opening)
                    l1 = i18n.i18n('Found open "{" with no matching closing brace')
                    l2 = i18n.i18n('Maybe there is an extra "%s" at %s') % (
										tok.type, tok.pos_begin.row_col(),)
                    raise GbsParserException(i18n.i18n('\n'.join([l1, l2])), open_area)

            # check there are no open parens at EOF
            if tok.type == 'EOF' and len(open_parens) > 0:
                opening = open_parens[-1]
                open_area = position.ProgramAreaNear(opening)
                if opening.type == '(':
                    msg = i18n.i18n('Found end of file but there are open parens yet')
                    raise GbsParserException(i18n.i18n(msg), open_area)
                elif opening.type == '{':
                    msg = i18n.i18n('Found end of file but there are open braces yet')
                    raise GbsParserException(i18n.i18n(msg), open_area)

            yield tok
            previous_token = tok

    def warn_if_similar_to_reserved(self, value, area):
        tl = value.lower()
        if tl in self.reserved_lower and self.reserved_lower[tl] != value:
            raise GbsParserException(i18n.i18n('Found: %s\nMaybe should be: %s') % (
                                value, self.reserved_lower[tl]), area)

class GbsParser(bnf_parser.Parser):
    "Parser that identifies common parsing errors in Gobstones source files."

    def __init__(self, syntax, *args, **kwargs):
        bnf_parser.Parser.__init__(self, syntax, *args, **kwargs)

    def parse_error(self, nonterminal, previous_token, token):
        "Raises a GbstonesParserException describing a parse error."
        area = position.ProgramAreaNear(token)
        if previous_token.type == 'lowerid' and token.type == '(':
            raise GbsParserException(i18n.i18n('Cannot call a function here'), area)
        elif previous_token.type == 'upperid' and token.type == '(':
            raise GbsParserException(i18n.i18n('Cannot call a procedure here'), area)
        elif previous_token.type == 'upperid' and token.type != '(':
            msg = i18n.i18n('Procedure name "%s" is missing a "("') % (previous_token.value,)
            raise GbsParserException(msg, area)
        elif token.type == 'EOF':
            raise GbsParserException(i18n.i18n('Premature end of input'), area)
        bnf_parser.Parser.parse_error(self, nonterminal, previous_token, token)

class GbsAnalyzer(bnf_parser.Analyzer):
    def __init__(self, grammar, warn=std_warn):
        bnf_parser.Analyzer.__init__(self, GbsLexer, GbsParser, bnf_contents=grammar, warn=warn)

def create_analizer(grammar_file):
    bnf = grammar.i18n.translate(read_file(grammar_file))
    return GbsAnalyzer(bnf)

def check_grammar_conflicts(grammar_file):
    """Checks if the BNF grammar has any conflict (an LL(1) prediction
       with two productions)."""
    create_analizer(grammar_file).parser.check_conflicts()

def parse_string(string, filename='...', toplevel_filename=None, grammar_file=XGbsGrammarFile):
    "Parse a string and return an abstract syntax tree."
    analyzer = create_analizer(grammar_file)
    parsing_stream = analyzer.parse(string, filename)
    start_pos = position.Position(string, filename)
    tree = ast.ASTBuilder(start_pos).build_ast_from(parsing_stream)
    tree.source_filename = filename
    if toplevel_filename is None:
        tree.toplevel_filename = tree.source_filename
    else:
        tree.toplevel_filename = toplevel_filename
    return tree

def prelude_for_file(filename):
    prelude_basename = i18n.i18n('Prelude') + '.gbs'
    prelude_filename = os.path.join(os.path.dirname(filename), prelude_basename)
    if os.path.exists(prelude_filename) and os.path.basename(filename) != prelude_basename:
        return prelude_filename
    else:
        return None

def get_names(program_text):
    regexp = re.compile("(?:(type)\s*([A-Z][A-Za-z_']*)|(?:(procedure)\s*([A-Z][A-Za-z_']*)|(function)[\s]*([a-z][A-Za-z_']*))\s*\(\s*([^)]*?)\s*\))")
    matches = regexp.findall(program_text)
    names = {}
    for parts in matches:
        nametype, name = filter(lambda x: x != '', parts[0:6])
        names[name] = { "type" : nametype}
        if nametype in ["function", "procedure"]:
            parameters = parts[6].replace(" ", "").replace("\n", "").replace("\t", "").split(",")
            names[name]["parameters"] = parameters
    return  names

def parse_names(string, filename, toplevel_filename=None, grammar_file=XGbsGrammarFile):
    names = get_names(string)
    prelude_filename = prelude_for_file(filename)
    if prelude_filename is not None:
        names.update(get_names(open(prelude_filename).read()))
    return names

def parse_string_try_prelude(string, filename, toplevel_filename=None, grammar_file=XGbsGrammarFile):
    main_program = parse_string(string, filename, toplevel_filename, grammar_file)

    prelude_filename = prelude_for_file(filename)
    if prelude_filename is not None:
        prelude_barename = i18n.i18n('Prelude')
        pos = position.Position(string, filename)
        main_imports = main_program.children[1].children
        main_imports.insert(0, ast.ASTNode([
            'import',
            bnf_parser.Token('upperid', prelude_barename, pos, pos),
            ast.ASTNode([
                bnf_parser.Token('lowerid', '*', pos, pos),
            ], pos, pos)
        ], pos, pos))

    return main_program

def parse_file(filename, grammar_file=XGbsGrammarFile):
    "Parse a file and return an abstract syntax tree."
    return parse_string_try_prelude(read_file(filename), filename, grammar_file)

def token_stream(string, grammar_file):
    return create_analizer(grammar_file).lexer.pure_tokenize(string)
