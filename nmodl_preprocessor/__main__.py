# Copyright (c) 2023 David McDougall
# Released under the MIT License

from pathlib import Path
from types import SimpleNamespace
import argparse
import math
import nmodl.ast
import nmodl.dsl
import nmodl.symtab
import re
import textwrap

from nmodl_preprocessor.utils import *
from nmodl_preprocessor.rw_patterns import RW_Visitor
from nmodl_preprocessor import nmodl_to_python

issue_tracker = "https://github.com/ctrl-z-9000-times/nmodl_preprocessor/issues"

parser = argparse.ArgumentParser(prog='nmodl_preprocessor',
    description="""Optimize NMODL files for the NEURON simulator""",
    epilog=f"""Please report any problems to:\n{issue_tracker}""",
    formatter_class=argparse.RawDescriptionHelpFormatter)

parser.add_argument('input_path', type=str,
        help="input filename or directory of nmodl files")

parser.add_argument('output_path', type=str,
        help="output filename or directory for nmodl files")

parser.add_argument('--celsius', type=float, default=None,
        help="temperature of the simulation")

args = parser.parse_args()

# Find and sanity check all files to be processed.
input_path  = Path(args.input_path).resolve()
output_path = Path(args.output_path).resolve()
process_files   = [] # List of pairs of (source, destination)
copy_files      = [] # List of pairs of (source, destination)
assert input_path.exists()
if input_path.is_file():
    assert input_path.suffix == '.mod'
    if output_path.is_dir():
        output_path = output_path.joinpath(input_path.name)
    process_files.append((input_path, output_path))
elif input_path.is_dir():
    if not output_path.exists():
        # Make the directory if it doesn't exist.
        if output_path.parent.is_dir():
            output_path.mkdir()
    assert output_path.is_dir()
    for input_file in input_path.iterdir():
        output_file = output_path.joinpath(input_file.name)
        if input_file.suffix == '.mod':
            process_files.append((input_file, output_file))
        elif input_file.suffix in ('.hoc', '.ses'):
            copy_files.append((input_file, output_file))
else: raise RuntimeError('Unreachable')

# Main Loop.
for input_file, output_file in process_files:
    assert input_file != output_file
    print(f'Read file: "{input_file}"')
    with open(input_file, 'rt') as f:
        nmodl_text = f.read()
    def print_verbose(*strings, **kwargs):
        print(input_file.name+':', *strings, **kwargs)

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
    sym_table           = AST.get_symbol_table()
    sym_type            = nmodl.symtab.NmodlType
    get_vars_with_prop  = lambda prop: set(STR(x.get_name()) for x in sym_table.get_variables_with_properties(prop))
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
    # statements can not be analysed. Assume that all symbols in VERBATIM
    # blocks are both read from and written to. Do not attempt to alter the
    # source code inside of VERBATIM blocks.
    if lookup(ANT.VERBATIM):
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
    assigned_units = {name: '' for name in assigned_vars}
    for stmt in lookup(ANT.ASSIGNED_DEFINITION):
        if stmt.unit:
            assigned_units[STR(stmt.name)] = STR(stmt.unit)
    # 
    rw = RW_Visitor()
    rw.visit_program(AST)
    # Split the document into its top-level blocks for easier manipulation.
    blocks_list = [SimpleNamespace(node=x, text=nmodl.to_nmodl(x)) for x in AST.blocks]
    blocks      = {get_block_name(x.node): x for x in blocks_list}

    # Inline the parameters.
    parameters = {}
    for name in (parameter_vars - external_vars - verbatim_vars - rw.all_writes):
        for node in sym_table.lookup(name).get_nodes():
            if node.is_param_assign() and node.value is not None:
                value = float(STR(node.value))
                units = ('('+STR(node.unit.name)+')') if node.unit else ''
                parameters[name] = (value, units)
                print_verbose(f'inline PARAMETER: {name} = {value} {units}')

    # Inline celsius if it's given, and override any default parameter value.
    if 'celsius' in verbatim_vars:  args.celsius = None # Can not inline into VERBATIM blocks.
    if 'celsius' not in parameter_vars: args.celsius = None # Parameter is not used.
    if args.celsius is not None:
        parameters['celsius'] = (args.celsius, '(degC)')
        print_verbose(f'inline PARAMETER: celsius = {args.celsius} (degC)')

    # Inline Q10. Detect and inline assigned variables with a constant value
    # which is set in the initial block.
    assigned_const_value = {}
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
        global_scope  = {"math": math} # Include the standard math library.
        initial_scope = {}
        # Represent unknown external input values as NaN's.
        for name in external_vars:
            global_scope[name] = math.nan
        # 
        for name, (value, units) in parameters.items():
            global_scope[name] = value
        # 
        if can_exec:
            try:
                exec(x.pycode, global_scope, initial_scope)
            except:
                pycode = prepend_line_numbers(x.pycode.rstrip())
                print_verbose("error: while executing INITIAL block:\n" + pycode)
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
            value = initial_scope[name]
            units = assigned_units[name]
            assigned_const_value[name] = (value, "")
            print_verbose(f'inline ASSIGNED with constant value: {name} = {value} {units}')

    # Convert assigned variables into local variables as able.
    assigned_to_local = set(assigned_vars) - set(external_vars) - set(assigned_const_value)
    # Search for variables whose persistent state is ignored/overwritten.
    for block_name, read_variables in rw.reads.items():
        assigned_to_local -= read_variables
    # Check for verbatim statements referencing this variable, which can not be analysed correctly.
    assigned_to_local -= verbatim_vars
    # 
    for name in assigned_to_local:
        print_verbose(f'convert from ASSIGNED to LOCAL: {name}')

    ############################################################################

    # Regenerate the PARAMETER block without the inlined parameters.
    if block := blocks.get('PARAMETER', None):
        new_lines = []
        for stmt in block.node.statements:
            if stmt.is_param_assign():
                name = STR(stmt.name)
                if name == 'celsius':
                    pass
                elif name in parameters:
                    continue
            stmt_nmodl = nmodl.to_nmodl(stmt)
            new_lines.append(stmt_nmodl)
        block.text = 'PARAMETER {\n' + '\n'.join('    ' + x for x in new_lines) + '\n}'

    # Regenerate the ASSIGNED block without the removed symbols.
    if block := blocks.get('ASSIGNED', None):
        remove_assigned = set(assigned_to_local) | set(assigned_const_value)
        new_lines = []
        for stmt in block.node.definitions:
            if not (stmt.is_assigned_definition() and STR(stmt.name) in remove_assigned):
                stmt_nmodl = nmodl.to_nmodl(stmt)
                new_lines.append(stmt_nmodl)
        block.text = 'ASSIGNED {\n' + '\n'.join('    ' + x for x in new_lines) + '\n}'

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
        substitutions.update(assigned_const_value)
        for name, (value, units) in substitutions.items():
            # The assignment to this variable is still present, it's just
            # converted to a local variable. The compiler should be able to
            # eliminate this unused code.
            if block.node.is_initial_block() and name in assigned_const_value:
                continue
            # Delete references to the symbol from TABLE statements.
            table_regex = rf'\bTABLE\s+(\w+\s*,\s*)*\w+\s+DEPEND\s+(\w+\s*,\s*)*{name}\b'
            block.text = re.sub(
                    table_regex,
                    lambda m: re.sub(rf',\s*{name}\b', '', m.group()),
                    block.text)
            # Substitute the symbol from general code.
            value = str(value) + units
            block.text = re.sub(rf'\b{name}\b', value, block.text)

    # Check the temperature in the INITIAL block.
    if args.celsius is not None:
        if block := blocks.get('INITIAL', None):
            signature, start, body = block.text.partition('{')
            check_temp = f"\n    VERBATIM\n    assert(celsius == {args.celsius});\n    ENDVERBATIM\n"
            block.text = signature + start + check_temp + body

    # Insert new LOCAL statements to replace the removed assigned variables.
    new_locals = {} # Maps from block name to set of names of new local variables.
    if assigned_const_value:
        new_locals['INITIAL'] = set(assigned_const_value.keys())
    for block_name, write_variables in rw.writes.items():
        if converted_variables := assigned_to_local & write_variables:
            new_locals.setdefault(block_name, set()).update(converted_variables)
    # 
    for block_name, local_names in new_locals.items():
        block = blocks[block_name]
        signature, start, body = block.text.partition('{')
        names = ', '.join(sorted(local_names))
        body  = textwrap.indent(body, '    ')
        block.text = signature + '{\n    LOCAL ' + names + '\n    {' + body + '\n}'

    # Find any local statements in the top level scope and move them to the top
    # of the file. Local variables must be declared before they're used, and
    # inlining functions can cause them to be used before they were originally declared.
    blocks_list.sort(key=lambda x: not (
            x.node.is_model() or x.node.is_block_comment() or x.node.is_local_list_statement()))

    # Join the top-level blocks back into one big string and save it to the output file.
    nmodl_text = '\n\n'.join(x.text for x in blocks_list) + '\n'
    print(f'Write file: "{output_file}"')
    with output_file.open('w') as f:
        f.write(nmodl_text)

# Copy over any miscellaneous files from the source directory.
for src, dst in copy_files:
    import shutil
    print(f'Copy associated file: "{src.name}"')
    shutil.copy(src, dst)

# Symbol for the installation script to import and call.
_placeholder = lambda: None

