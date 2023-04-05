import argparse
import math
import nmodl.ast
import nmodl.dsl
import nmodl.symtab
import numpy as np
import re
import textwrap
import nmodl_to_python
from pathlib import Path
from types import SimpleNamespace
STR = lambda x: str(x).strip() # nmodl sometimes leaves trailing whitespace on stuff.

"""
TODO docs
"""

# TODO: Add author & copyright strings here.

parser = argparse.ArgumentParser(
        description="TODO",)

parser.add_argument('input_path', type=str,
        help="input filename or directory of nmodl files")

parser.add_argument('output_path', type=str,
        help="output filename or directory for nmodl files")

parser.add_argument('-v', '--verbose', action='count')

parser.add_argument('-x', '--strict', action='store_true',
        help="Maintain strict compatibility with the original NEURON semantics.")

parser.add_argument('--celsius', type=float, default=None,
        help="")

args = parser.parse_args()

# Find and sanity check all files to be processed.
input_path  = Path(args.input_path).resolve()
output_path = Path(args.output_path).resolve()
assert input_path.exists()
if input_path.is_file():
    assert input_path.suffix == '.mod'
    if output_path.is_dir():
        output_path = output_path.joinpath(input_path.name)
    input_path  = [input_path]
    output_path = [output_path]
elif input_path.is_dir():
    if not output_path.exists():
        pass # TODO: Make the directory if it doesn't exist.
    assert output_path.is_dir()
    input_path  = [x for x in input_path.iterdir() if x.suffix == '.mod']
    output_path = [output_path.joinpath(x.name) for x in input_path]
else: raise RuntimeError('Unreachable')

# Iterate over the files and read each of them.
for input_file, output_file in zip(input_path, output_path):
    assert input_file != output_file
    with open(input_file, 'rt') as f:
        nmodl_text = f.read()
    def print_verbose(*strings, **kwargs):
        if args.verbose:
            print(input_file.name+':', *strings, **kwargs)
    print_verbose(f'opened: "{input_file}"')

    # Parse the nmodl file into an AST.
    ANT = nmodl.ast.AstNodeType
    AST = nmodl.NmodlDriver().parse_string(nmodl_text)

    # Always inline all of the functions and procedures.
    nmodl.symtab.SymtabVisitor().visit_program(AST)
    nmodl.dsl.visitor.InlineVisitor().visit_program(AST)
    # Reload the modified AST so that the nmodl library starts from a clean state.
    nmodl_text = nmodl.to_nmodl(AST)
    AST = nmodl.NmodlDriver().parse_string(nmodl_text)

    # nmodl.ast.view(AST)             # Useful for debugging.
    # print(AST.get_symbol_table())   # Useful for debugging.

    # Extract important data from the AST.
    visitor             = nmodl.dsl.visitor.AstLookupVisitor()
    lookup              = lambda n: visitor.lookup(AST, n)
    nmodl.symtab.SymtabVisitor().visit_program(AST)
    symtab              = AST.get_symbol_table()
    sym_type            = nmodl.symtab.NmodlType
    get_vars_with_prop  = lambda prop: set(STR(x.get_name()) for x in symtab.get_variables_with_properties(prop))
    neuron_vars         = get_vars_with_prop(sym_type.extern_neuron_variable)
    read_ion_vars       = get_vars_with_prop(sym_type.read_ion_var)
    write_ion_vars      = get_vars_with_prop(sym_type.write_ion_var)
    nonspecific_vars    = get_vars_with_prop(sym_type.nonspecific_cur_var)
    range_vars          = get_vars_with_prop(sym_type.range_var)
    global_vars         = get_vars_with_prop(sym_type.global_var)
    parameters          = get_vars_with_prop(sym_type.param_assign)
    assigned_vars       = get_vars_with_prop(sym_type.assigned_definition)
    state_vars          = get_vars_with_prop(sym_type.state_var)
    functions           = get_vars_with_prop(sym_type.function_block)
    procedures          = get_vars_with_prop(sym_type.procedure_block)
    solve_blocks        = get_vars_with_prop(sym_type.to_solve)
    inlined_blocks      = [x for x in (functions | procedures) if x not in solve_blocks]
    # Find all symbols which are provided by or are visible to the larger NEURON simulation.
    external_vars = (
            neuron_vars |
            read_ion_vars |
            write_ion_vars |
            nonspecific_vars |
            range_vars |
            global_vars |
            state_vars |
            functions |
            procedures)
    # Split the document into its top-level blocks for easier manipulation.
    blocks_list = [SimpleNamespace(node=x, text=nmodl.to_nmodl(x)) for x in AST.blocks]
    blocks      = {} # Indexed by block name.
    for x in blocks_list:
        name = getattr(x.node, 'name', None)
        if name is None:
            try:
                name = x.node.get_nmodl_name()
            except RuntimeError:
                name = x.node.get_node_type_name()
        blocks[STR(name)] = x

    # Inline the parameters.
    substitutions = {}
    for name in (parameters - external_vars):
        for node in symtab.lookup(name).get_nodes():
            if node.is_param_assign() and node.value is not None:
                value = float(STR(node.value))
                if node.unit is not None:
                    substitutions[name] = (value, STR(node.unit.name))
                else:
                    substitutions[name] = (value, "")
                print_verbose(f'inline and remove parameter: {name} = {value}')

    # Inline celsius if it's given.
    if args.celsius is not None:
        substitutions['celsius'] = (args.celsius, 'degC')
        print_verbose(f'inline and remove parameter: celsius = {args.celsius}')

    # Inline Q10. Detect and inline assigned variables with a constant value
    # which is set in the initial block.
    if initial_block := blocks.get('INITIAL', None):
        x = nmodl_to_python.PyGenerator()
        x.visit_initial_block(initial_block.node)
        global_scope  = {}
        initial_scope = {}
        # Represent unknown external input values as NaN's.
        for name in external_vars:
            global_scope[name] = math.nan
        # 
        for name, value in substitutions.items():
            global_scope[name] = value[0]
        # 
        try:
            exec(x.pycode, global_scope, initial_scope)
        except:
            pycode = '\n'.join(str(i+1).rjust(2) + ": " + line for i, line in enumerate(x.pycode.split('\n'))) # Prepend line numbers.
            print('While executing:')
            print(pycode)
            raise
        # Filter out any assignments that were made with unknown input values.
        initial_scope = dict(x for x in initial_scope.items() if not math.isnan(x[1]))
        # 
        class WriteDetector(nmodl.dsl.visitor.AstVisitor):
            """ Determine which symbols each top-level block writes to. """
            def visit_program(self, node):
                self.current_block = None
                self.writes_to = {} # Maps from block name to set of variable names.
                node.visit_children(self)

            def visit_statement_block(self, node): # Top level code blocks
                if node.parent.is_procedure_block():
                    self.current_block = STR(node.parent.name)
                else:
                    self.current_block = STR(node.parent.get_nmodl_name())
                node.visit_children(self)

            def visit_binary_expression(self, node):
                if node.op.eval() == '=':
                    name = STR(node.lhs.name.get_node_name())
                    self.writes_to.setdefault(self.current_block, set()).add(name)
        # Do not inline variables if they are written to in other blocks besides the INITIAL block.
        x = WriteDetector()
        x.visit_program(AST)
        x.writes_to.pop('INITIAL', None)
        runtime_writes_to = set()
        for block_name, writes_to in x.writes_to.items():
            runtime_writes_to.update(writes_to)
        # 
        initial_assigned = {}
        for name in ((assigned_vars & set(initial_scope)) - runtime_writes_to):
            # TODO: lookup the units in the symtab
            value = initial_scope[name]
            initial_assigned[name] = (value, "")
            print_verbose(f'make constant: {name} = {value}')

        substitutions.update(initial_assigned)

    # Convert assigned variables into local variables as able.
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

        def visit_neuron_block(self, node):
            pass

        def visit_function_block(self, node):
            pass

        def visit_statement_block(self, node): # Top level code blocks
            # 
            if name := getattr(node.parent, 'name', None):
                self.current_block = STR(name)
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
    localize = localize_candidates - x.read_first
    for name in localize:
        print_verbose(f'converted from assigned to local: {name}')

    ############################################################################

    # Regenerate the PARAMETER block without the inlined parameters.
    if block := blocks.get('PARAMETER', None):
        new_lines = []
        for stmt in block.node.statements:
            if not (stmt.is_param_assign() and STR(stmt.name) in substitutions):
                stmt_nmodl = nmodl.to_nmodl(stmt)
                new_lines.append(stmt_nmodl)
        block.text = 'PARAMETER {\n' + '\n'.join('    ' + x for x in new_lines) + '\n}'

    # Regenerate the ASSIGNED block without the localized variables.
    if block := blocks.get('ASSIGNED', None):
        remove_assigned = localize | set(initial_assigned)
        new_lines = []
        for stmt in block.node.definitions:
            if not (stmt.is_assigned_definition() and STR(stmt.name) in remove_assigned):
                stmt_nmodl = nmodl.to_nmodl(stmt)
                new_lines.append(stmt_nmodl)
        block.text = 'ASSIGNED {\n' + '\n'.join('    ' + x for x in new_lines) + '\n}'

    # Check the temperature in the INITIAL block.
    if args.celsius is not None:
        if block := blocks.get('INITIAL', None):
            f"VERBATIM\n    assert(celsius == {args.celsius});\n    ENDVERBATIM\n"
            # block.text = 1/0

    # Substitute the parameters with their values.
    for block in blocks_list:
        if block.node.is_model(): continue
        if block.node.is_block_comment(): continue
        if block.node.is_neuron_block(): continue
        if block.node.is_unit_block(): continue
        if block.node.is_unit_state(): continue
        if block.node.is_param_block(): continue
        if block.node.is_state_block(): continue
        if block.node.is_assigned_block(): continue
        for name, value in substitutions.items():
            if block.node.is_initial_block() and name in initial_assigned:
                pass # The assignment to this variable is still present.
            else:
                if value[1]:
                    value = f'{value[0]}({value[1]})'
                else:
                    value = f'{value[0]}'
                block.text = re.sub(rf'\b{name}\b', value, block.text)

    # Insert new LOCAL statements to replace the assigned variables.
    new_locals = {} # Maps from block name to set of names of new local variables.
    new_locals.setdefault('INITIAL', set()).update(initial_assigned.keys())
    for name in localize:
        for block in x.blocks[name]:
            new_locals.setdefault(block, set()).add(name)
    # 
    for block_name, local_names in new_locals.items():
        block = blocks[block_name]
        signature, start, body = block.text.partition('{')
        names       = ', '.join(sorted(local_names))
        body  = textwrap.indent(body, '    ')
        block.text = signature + '{\n    LOCAL ' + names + '\n    {' + body + '\n}'

    nmodl_text = '\n\n'.join(x.text for x in blocks_list) + '\n'
    with output_file.open('w') as f:
        f.write(nmodl_text)
        print_verbose(f'saved to: "{output_file}"')

