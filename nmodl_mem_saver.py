import argparse
import nmodl.ast
import nmodl.dsl
import nmodl.symtab
import numpy as np
import re
import textwrap
from pathlib import Path
STR = lambda x: str(x).strip() # nmodl sometimes leaves trailing whitespace on stuff.

"""
TODO docs
"""

# TODO: Add author & copyright strings here.

parser = argparse.ArgumentParser(prog='lti_sim',
        description="TODO",)

parser.add_argument('input_path', type=str,
        help="input filename or directory of nmodl files")

parser.add_argument('output_path', type=str,
        help="output filename or directory for nmodl files")

parser.add_argument('-v', '--verbose', action='count')

parser.add_argument('-x', '--strict', action='store_true',
        help="Maintain strict compatibility with the original NEURON semantics.")

parser.add_argument('--dt', type=float, default=None,
        help="milliseconds")
parser.add_argument('--celsius', type=float, default=None,
        help="")

args = parser.parse_args()

# Find and sanity check all files to be processed.
input_path  = Path(args.input_path) .resolve()
output_path = Path(args.output_path).resolve()
assert input_path.exists()
if input_path.is_file():
    assert input_path.suffix == '.mod'
    if output_path.is_dir():
        output_path = output_path.joinpath(input_path.name)
    input_path  = [input_path]
    output_path = [output_path]
elif input_path.is_dir():
    assert output_path.is_dir()
    input_path  = [x for x in input_path.iterdir() if x.suffix == '.mod']
    output_path = [output_path.joinpath(x.name) for x in input_path]
else: raise RuntimeError('Unreachable')

# Iterate over the files and read each of them.
for input_file, output_file in zip(input_path, output_path):
    assert input_file != output_file
    with open(input_file, 'rt') as f:
        nmodl_text = f.read()
    def print_verbose(string):
        if args.verbose:
            print(f'{input_file.name}: {string}')

    # Remove any independent statements.
    # They're unnecessary and they can cause nmodl to fail.
    nmodl_text = re.sub(r'INDEPENDENT\s*{[^}]*}', '', nmodl_text)

    # Parse the nmodl file into an AST.
    ANT = nmodl.ast.AstNodeType
    AST = nmodl.dsl.NmodlDriver().parse_string(nmodl_text)

    # Always inline all of the functions and procedures.
    nmodl.symtab.SymtabVisitor().visit_program(AST)
    nmodl.dsl.visitor.InlineVisitor().visit_program(AST)
    nmodl_text = nmodl.dsl.to_nmodl(AST)

    # nmodl.ast.view(AST)             # Useful for debugging.
    # print(AST.get_symbol_table())   # Useful for debugging.

    # Extract important data from the AST.
    visitor             = nmodl.dsl.visitor.AstLookupVisitor()
    lookup              = lambda n: visitor.lookup(AST, n)
    nmodl.symtab.SymtabVisitor().visit_program(AST)
    symtab              = AST.get_symbol_table()
    sym_type            = nmodl.symtab.NmodlType
    get_vars_with_prop  = lambda prop: [STR(x.get_name()) for x in symtab.get_variables_with_properties(prop)]
    neuron_vars         = get_vars_with_prop(sym_type.extern_neuron_variable)
    read_ion_vars       = get_vars_with_prop(sym_type.read_ion_var)
    write_ion_vars      = get_vars_with_prop(sym_type.write_ion_var)
    nonspecific_vars    = get_vars_with_prop(sym_type.nonspecific_cur_var)
    range_vars          = get_vars_with_prop(sym_type.range_var)
    parameters          = get_vars_with_prop(sym_type.param_assign)
    assigned_vars       = get_vars_with_prop(sym_type.assigned_definition)
    state_vars          = get_vars_with_prop(sym_type.state_var)
    functions           = get_vars_with_prop(sym_type.function_block)
    procedures          = get_vars_with_prop(sym_type.procedure_block)
    initial_block       = lookup(ANT.INITIAL_BLOCK)
    breakpoint_block    = lookup(ANT.BREAKPOINT_BLOCK)
    derivative_block    = lookup(ANT.DERIVATIVE_BLOCK)
    kinetic_block       = lookup(ANT.KINETIC_BLOCK)
    linear_block        = lookup(ANT.LINEAR_BLOCK)
    non_linear_block    = lookup(ANT.NON_LINEAR_BLOCK)
    solver_blocks       = [STR(x.block_name) for x in lookup(ANT.SOLVE_BLOCK)]

    def find_next_closing_brace(text, depth = 0):
        """ Find the next closing curly brace, accounting for any nested blocks. """
        verbatim = False
        for brace in re.finditer(r'{|}|VERBATIM|ENDVERBATIM', text):
            if brace.group() == '{':
                if not verbatim:
                    depth += 1
            elif brace.group() == '}':
                if not verbatim:
                    depth -= 1
            elif brace.group() == 'VERBATIM':
                verbatim = True
            elif brace.group() == 'ENDVERBATIM':
                verbatim = False
            # 
            if depth == 0:
                break
        return brace.end() - 1

    # Remove all FUNCTION and PROCEDURE blocks because they've been inlined.
    # Except if they're the target of a SOLVE statement.
    # if not args.strict:
    #     remove_blocks = functions + procedures
    #     remove_blocks = [x for x in remove_blocks if x not in solver_blocks]
    #     for name in remove_blocks:
    #         match = re.search(rf'(FUNCTION|PROCEDURE)\s+{name}\b', nmodl_text)
    #         assert match
    #         head, tail = nmodl_text[:match.start()], nmodl_text[match.end():]
    #         end_of_block = find_next_closing_brace(tail)
    #         nmodl_text = head + tail[end_of_block + 1:]
    #         if args.verbose: print(f'inlined and removed block: {name}')

    # Inline all of the parameters.
    replace_parameters = {}
    if not args.strict:
        for name in parameters:
            if name in range_vars: continue
            if name in read_ion_vars: continue
            if name in write_ion_vars: continue
            if name in state_vars: continue
            if name in neuron_vars: continue
            symbol = symtab.lookup(name)
            value = symbol.get_node().value
            units = symbol.get_node().unit
            if value is not None:
                if units is not None:
                    replace_parameters[name] = f'{value}({units.name})'
                else:
                    replace_parameters[name] = f'{value}'

    # Inline celsius if it's given.
    if args.celsius is not None:
        replace_parameters['celsius'] = f'{args.celsius}(degC)'

    # Remove the inlined parameters from the PARAMETERS block.
    if old_block := lookup(ANT.PARAM_BLOCK):
        old_block = old_block[0]
        new_lines = []
        for stmt in old_block.statements:
            if stmt.is_param_assign() and STR(stmt.name) in replace_parameters:
                continue
            else:
                stmt_nmodl = nmodl.dsl.to_nmodl(stmt)
                new_lines.append(stmt_nmodl)
        new_block = '\n'.join('    ' + x for x in new_lines)
        new_block = 'PARAMETER {\n' + new_block + '\n}\n'
        nmodl_text = re.sub(r'PARAMETER\s*{[^}]*}', new_block, nmodl_text)

    # Substitute the parameters with their values.
    for name, value in replace_parameters.items():
        nmodl_text = re.sub(rf'\b{name}\b', value, nmodl_text)
        print_verbose(f'inlined and removed parameter: {name} = {value}')

    # Inline Q10.
    pass

    # Convert assigned variables into local variables as able.
    # First determine which variables are externally visible, or are in any way
    # shared with the larger NEURON simulation. Do not apply this optimization
    # to any such variables.
    external_vars = (neuron_vars +
                    read_ion_vars +
                    write_ion_vars +
                    nonspecific_vars +
                    state_vars +
                    range_vars)
    localize_candidates = set(assigned_vars) - set(external_vars)
    # Search for variables with no persistent state.
    class OverwriteDetector(nmodl.dsl.visitor.AstVisitor):
        """ This visitor detects when a variable is written to without first being read from. """
        def visit_program(self, node):
            self.read_first  = set()
            self.write_first = set()
            # Also record which blocks each variable is present in.
            self.current_block = None
            self.blocks = {} # Maps from variable name to set of block names.
            super().visit_program(node)
            self.overwrites = self.write_first - self.read_first

        def visit_statement_block(self, node): # Top level code blocks
            if node.parent.is_neuron_block():     return
            if node.parent.is_function_block():   return
            # 
            if node.parent.is_procedure_block():
                self.current_block = STR(node.parent.name)
            else:
                self.current_block = STR(node.parent.get_nmodl_name())
            self.variables_seen = set()
            node.accept(self)

        def first_access(self, name):
            if name not in localize_candidates: return False # Optimization.
            first_access = name not in self.variables_seen
            if first_access:
                self.variables_seen.add(name)
                self.blocks.setdefault(name, set()).add(self.current_block)
            return first_access

        # TODO: Consider special cases for "if" statements?

        def visit_binary_expression(self, node):
            # Mark the left hand side variable of this assignment as being written to.
            if node.op.eval() == '=':
                name = STR(node.lhs.name.get_node_name())
                if self.first_access(name):
                    self.write_first.add(name)
                # And recursively mark all variables on right hand side as being read from.
                node.rhs.accept(self)
            else:
                node.lhs.accept(self)
                node.rhs.accept(self)

        def visit_var_name(self, node):
            # Mark this variable as being read from.
            name = STR(node.name.get_node_name())
            if self.first_access(name):
                self.read_first.add(name)

    x = OverwriteDetector()
    x.visit_program(AST)
    localize = x.overwrites.intersection(localize_candidates)

    # Remove the localized variables from the ASSIGNED block.
    if old_block := lookup(ANT.ASSIGNED_BLOCK):
        old_block = old_block[0]
        new_lines = []
        for stmt in old_block.definitions:
            if stmt.is_assigned_definition() and STR(stmt.name) in localize:
                continue
            else:
                stmt_nmodl = nmodl.dsl.to_nmodl(stmt)
                new_lines.append(stmt_nmodl)
        new_block = '\n'.join('    ' + x for x in new_lines)
        new_block = 'ASSIGNED {\n' + new_block + '\n}\n'
        nmodl_text = re.sub(r'ASSIGNED\s*{[^}]*}', new_block, nmodl_text)

    # Insert new LOCAL statements into each code block.
    new_locals = {} # Maps from block name to set of names of new local variables.
    for name in localize:
        for block in x.blocks[name]:
            new_locals.setdefault(block, set()).add(name)
        print_verbose(f'converted from assigned to local: {name}')
    for block, names in new_locals.items():
        # Find the block in the nmodl text.
        match = re.search(rf'\b{block}\b[^{{]*{{', nmodl_text)
        if not match:
            continue
        head  = nmodl_text[:match.end()]
        tail  = nmodl_text[match.end():]
        end_of_block = find_next_closing_brace(tail, depth=1)
        body  = tail[:end_of_block]
        tail  = tail[end_of_block:]
        tab   = ' '*4
        names = ', '.join(sorted(names))
        body  = textwrap.indent(body, tab)
        nmodl_text = f'{head}\n{tab}LOCAL {names}\n{tab}{{{body}{tab}}}\n{tail}'

    with output_file.open('w') as f:
        f.write(nmodl_text)
        print_verbose(f'saved to: "{output_file}"')

