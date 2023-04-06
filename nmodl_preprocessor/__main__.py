from pathlib import Path
from types import SimpleNamespace
import argparse
import math
import nmodl.ast
import nmodl.dsl
import nmodl.symtab
import numpy as np
import re
import textwrap

from utils import *
import nmodl_to_python
import rw_patterns

"""
TODO docs
"""

__version__ = "1.0.0"
__author__ = "David McDougall <dam1784[at]rit.edu>"
__license__ = "MIT"

parser = argparse.ArgumentParser(
        description="TODO",)

parser.add_argument('input_path', type=str,
        help="input filename or directory of nmodl files")

parser.add_argument('output_path', type=str,
        help="output filename or directory for nmodl files")

parser.add_argument('-v', '--verbose', action='count')

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

    # Remove INDEPENDENT statements because they're unnecessary and the nmodl library does not like them.
    nmodl_text = re.sub(r'\bINDEPENDENT\b\s*{[^{}]*}', '', nmodl_text)

    # Parse the nmodl file into an AST.
    ANT = nmodl.ast.AstNodeType
    AST = nmodl.NmodlDriver().parse_string(nmodl_text)

    # Always inline all of the functions and procedures.
    nmodl.symtab.SymtabVisitor().visit_program(AST)
    nmodl.dsl.visitor.InlineVisitor().visit_program(AST)
    # Reload the modified AST so that the nmodl library starts from a clean state.
    nmodl_text = nmodl.to_nmodl(AST)
    AST = nmodl.NmodlDriver().parse_string(nmodl_text)
    nmodl.symtab.SymtabVisitor().visit_program(AST)

    # nmodl.ast.view(AST)             # Useful for debugging.
    # print(AST.get_symbol_table())   # Useful for debugging.

    # Extract important data from the AST.
    visitor             = nmodl.dsl.visitor.AstLookupVisitor()
    lookup              = lambda n: visitor.lookup(AST, n)
    symtab              = AST.get_symbol_table()
    sym_type            = nmodl.symtab.NmodlType
    get_vars_with_prop  = lambda prop: set(STR(x.get_name()) for x in symtab.get_variables_with_properties(prop))
    neuron_vars         = get_vars_with_prop(sym_type.extern_neuron_variable)
    read_ion_vars       = get_vars_with_prop(sym_type.read_ion_var)
    write_ion_vars      = get_vars_with_prop(sym_type.write_ion_var)
    nonspecific_vars    = get_vars_with_prop(sym_type.nonspecific_cur_var)
    range_vars          = get_vars_with_prop(sym_type.range_var)
    global_vars         = get_vars_with_prop(sym_type.global_var)
    parameter_vars      = get_vars_with_prop(sym_type.param_assign)
    assigned_vars       = get_vars_with_prop(sym_type.assigned_definition)
    state_vars          = get_vars_with_prop(sym_type.state_var)
    pointer_vars        = get_vars_with_prop(sym_type.pointer_var) | get_vars_with_prop(sym_type.bbcore_pointer_var)
    functions           = get_vars_with_prop(sym_type.function_block)
    procedures          = get_vars_with_prop(sym_type.procedure_block)
    solve_blocks        = get_vars_with_prop(sym_type.to_solve)
    inlined_blocks      = [x for x in (functions | procedures) if x not in solve_blocks]
    # Find all symbols that are referenced in VERBATIM blocks.
    verbatim_vars = set()
    for stmt in lookup(ANT.VERBATIM):
        for symbol in re.finditer(r'\b\w+\b', nmodl.to_nmodl(stmt)):
            verbatim_vars.add(symbol.group())
    # Let's get this warning out of the way. As chunks of C/C++ code, VERBATIM
    # statements can not be analysed correctly. Assume that all symbols in
    # VERBATIM blocks are both read from and written to. Do not attempt to
    # alter the source code in any VERBATIM statements.
    if (parameter_vars | assigned_vars | {'celsius'}) & verbatim_vars:
        print_verbose('warning: VERBATIM may prevent optimization')
    # Find all symbols which are provided by or are visible to the larger NEURON simulation.
    external_vars = (
            neuron_vars |
            read_ion_vars |
            write_ion_vars |
            nonspecific_vars |
            range_vars |
            global_vars |
            state_vars |
            pointer_vars |
            functions |
            procedures)
    # 
    rw = rw_patterns.RW_Visitor()
    rw.visit_program(AST)
    # Split the document into its top-level blocks for easier manipulation.
    blocks_list = [SimpleNamespace(node=x, text=nmodl.to_nmodl(x)) for x in AST.blocks]
    blocks      = {get_block_name(x.node): x for x in blocks_list}

    # Inline the parameters.
    parameters = {}
    for name in (parameter_vars - external_vars - verbatim_vars):
        for node in symtab.lookup(name).get_nodes():
            if node.is_param_assign() and node.value is not None:
                value = float(STR(node.value))
                if node.unit is not None:
                    parameters[name] = (value, STR(node.unit.name))
                else:
                    parameters[name] = (value, "")
                print_verbose(f'inline parameter: {name} = {value}')

    # Inline celsius if it's given, overriding any default parameter value.
    if args.celsius is not None:
        if 'celsius' in verbatim_vars:
            args.celsius = None # Can not inline into VERBATIM blocks.
        else:
            parameters['celsius'] = (args.celsius, 'degC')
            print_verbose(f'inline temperature: celsius = {args.celsius}')

    # Inline Q10. Detect and inline assigned variables with a constant value
    # which is set in the initial block.
    initial_assigned = {}
    if initial_block := blocks.get('INITIAL', None):
        # Convert the INITIAL block into python.
        x = nmodl_to_python.PyGenerator()
        try:
            x.visit_initial_block(initial_block.node)
            can_exec = True
        except nmodl_to_python.VerbatimError:
            can_exec = False
        except nmodl_to_python.ComplexityError:
            can_exec = False
            print_verbose('warning: complex INITIAL block may prevent optimization')
        # 
        global_scope  = {}
        initial_scope = {}
        # Represent unknown external input values as NaN's.
        for name in external_vars:
            global_scope[name] = math.nan
        # 
        for name, value in parameters.items():
            global_scope[name] = value[0]
        # 
        if can_exec:
            try:
                exec(x.pycode, global_scope, initial_scope)
            except:
                pycode = '\n'.join(str(i+1).rjust(2) + ": " + line for i, line in enumerate(x.pycode.split('\n'))) # Prepend line numbers.
                print("While exec'ing:")
                print(pycode)
                raise
        # Filter out any assignments that were made with unknown input values.
        initial_scope = dict(x for x in initial_scope.items() if not math.isnan(x[1]))
        # Do not inline variables if they are written to in other blocks besides the INITIAL block.
        runtime_writes_to = set()
        for block_name, variables in rw.writes.items():
            if block_name != 'INITIAL':
                runtime_writes_to.update(variables)
        # 
        for name in ((assigned_vars & set(initial_scope)) - runtime_writes_to - verbatim_vars):
            # TODO: lookup the units in the symtab
            value = initial_scope[name]
            initial_assigned[name] = (value, "")
            print_verbose(f'inline ASSIGNED with constant value: {name} = {value}')

    # Convert assigned variables into local variables as able.
    localize = set(assigned_vars) - set(external_vars)
    # Search for variables whose persistent state is ignored/overwritten.
    for block_name, variables in rw.reads.items():
        localize -= variables
    # Check for verbatim statements referencing this variable, which can not be analysed correctly.
    localize -= verbatim_vars
    # 
    for name in localize:
        print_verbose(f'convert from ASSIGNED to LOCAL: {name}')

    ############################################################################

    # Regenerate the PARAMETER block without the inlined parameters.
    if block := blocks.get('PARAMETER', None):
        new_lines = []
        for stmt in block.node.statements:
            if not (stmt.is_param_assign() and STR(stmt.name) in parameters):
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
            # block.text = 1/0 # TODO!

    # Substitute the parameters with their values.
    for block in blocks_list:
        # Search for the blocks which contain code.
        if block.node.is_model(): continue
        if block.node.is_block_comment(): continue
        if block.node.is_neuron_block(): continue
        if block.node.is_unit_block(): continue
        if block.node.is_unit_state(): continue
        if block.node.is_param_block(): continue
        if block.node.is_state_block(): continue
        if block.node.is_assigned_block(): continue
        # 
        substitutions = dict(parameters)
        substitutions.update(initial_assigned)
        for name, (value, units) in substitutions.items():
            # The assignment to this variable is still present, it's just converted to a local variable.
            if block.node.is_initial_block() and name in initial_assigned:
                continue
            # Delete references to the symbol from TABLE statements.
            table_regex = rf'\bTABLE\s+(\w+\s*,\s*)*\w+\s+DEPEND\s+(\w+\s*,\s*)*{name}\b'
            block.text = re.sub(
                    table_regex,
                    lambda m: re.sub(rf',\s*{name}\b', '', m.group()),
                    block.text)
            # Substitued references to the symbol from general code.
            value = str(value)
            if units:
                value += f'({units})'
            block.text = re.sub(rf'\b{name}\b', value, block.text)

    # Insert new LOCAL statements to replace the assigned variables.
    new_locals = {} # Maps from block name to set of names of new local variables.
    if initial_assigned:
        new_locals['INITIAL'] = set(initial_assigned.keys())
    for block_name, variables in rw.writes.items():
        localize_variables = localize & variables
        if localize_variables:
            new_locals.setdefault(block_name, set()).update(localize_variables)
    # 
    for block_name, local_names in new_locals.items():
        block = blocks[block_name]
        signature, start, body = block.text.partition('{')
        names       = ', '.join(sorted(local_names))
        body  = textwrap.indent(body, '    ')
        block.text = signature + '{\n    LOCAL ' + names + '\n    {' + body + '\n}'

    # Join the top-level blocks back into one big string and save it to the output file.
    nmodl_text = '\n\n'.join(x.text for x in blocks_list) + '\n'
    with output_file.open('w') as f:
        f.write(nmodl_text)
        print_verbose(f'saved to: "{output_file}"')

