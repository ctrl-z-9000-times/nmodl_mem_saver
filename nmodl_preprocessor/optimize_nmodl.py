from sys import stderr
from types import SimpleNamespace
import math
import re
import textwrap

import nmodl.ast
import nmodl.dsl
import nmodl.symtab
ANT = nmodl.ast.AstNodeType

from nmodl_preprocessor.utils import *
from nmodl_preprocessor.rw_patterns import RW_Visitor
from nmodl_preprocessor.cpp_keywords import cpp_keywords
from nmodl_preprocessor import nmodl_to_python

# Don't remove parameters with these names, because of unexpected name conflicts
# caused by auto-generated initial values.
parameter_name_conflicts = {'y0', 'j0'}

def optimize_nmodl(input_file, output_file, external_symbols, celsius=None):
    assert input_file != output_file, "operation would overwrite input file"
    # 
    def print_verbose(*strings, **kwargs):
        print(input_file.name+':', *strings, **kwargs, file=stderr)
    def print_exception(exception):
        print_verbose(f"{type(exception).__name__}: {str(exception)}")
    # First read the file as binary and discard as much of it as possible.
    print_verbose(f'read file: "{input_file}"')
    with open(input_file, 'rb') as f:
        nmodl_text = f.read()

    # Remove comments in case contain invalid utf-8 text.
    nmodl_text = re.sub(rb'(?s)\bCOMMENT\b.*?\bENDCOMMENT\b', b'', nmodl_text)

    # Remove INDEPENDENT statements because they're unnecessary and the nmodl library does not like them.
    nmodl_text = re.sub(rb'\bINDEPENDENT\b\s*{[^{}]*}', b'', nmodl_text)

    # Parse the nmodl file into an AST.
    nmodl_text = nmodl_text.decode()
    try:
        AST = nmodl.NmodlDriver().parse_string(nmodl_text)
        nmodl.symtab.SymtabVisitor().visit_program(AST)
    except RuntimeError as error:
        print_exception(error)
        print_verbose("warning: could not parse and build symbol table")
        copy_files.append((input_file, output_file))
        return

    # nmodl.ast.view(AST)             # Useful for debugging.
    # print(AST.get_symbol_table())   # Useful for debugging.

    # Check for INCLUDE statements. It is unreasonable to find the included file.
    # The file is located in one of the following places (searched in this order):
    #     1) The current working directory,
    #     2) The directory of this nmodl file,
    #     3) The directories specified by the "MODL_INCLUDES" environment variable.
    # INCLUDE statements prevent all optimizations because they all rely on
    # having the complete NMODL source code.
    visitor = nmodl.dsl.visitor.AstLookupVisitor()
    lookup  = lambda ast_node_type: visitor.lookup(AST, ast_node_type)
    if lookup(ANT.INCLUDE):
        print_verbose('warning: INCLUDE prevent optimization')
        copy_files.append((input_file, output_file))
        return

    # Find all symbols that are referenced in VERBATIM blocks.
    verbatim_vars = set()
    for stmt in lookup(ANT.VERBATIM):
        for symbol in re.finditer(r'\b\w+\b', nmodl.to_nmodl(stmt)):
            verbatim_vars.add(symbol.group())
    verbatim_vars -= cpp_keywords
    # Let's get this warning out of the way. As chunks of arbitrary C/C++ code,
    # VERBATIM blocks can not be analysed. Assume that all symbols in VERBATIM
    # blocks are publicly visible and are both read from and written to.
    # Do not attempt to alter the source code inside of VERBATIM blocks.
    if verbatim_vars:
        print_verbose('warning: VERBATIM may prevent optimization')

    # Inline all of the functions and procedures
    if not verbatim_vars: # The NMODL library fails to correctly analyze VERBATIM blocks.
        try:
            nmodl.dsl.visitor.InlineVisitor().visit_program(AST)
        except RuntimeError as error:
            print_exception(error)
            print_verbose("warning: could not inline all functions and procedures")
        else:
            nmodl_text = nmodl.to_nmodl(AST)
        # Reload the modified AST so that the NMODL library starts from a clean state.
        AST    = nmodl.NmodlDriver().parse_string(nmodl_text)
        lookup = lambda ast_node_type: visitor.lookup(AST, ast_node_type)
        nmodl.symtab.SymtabVisitor().visit_program(AST)

    # Extract important data from the symbol table.
    sym_table           = AST.get_symbol_table()
    sym_type            = nmodl.symtab.NmodlType
    get_vars_with_prop  = lambda prop: set(STR(x.get_name()) for x in sym_table.get_variables_with_properties(prop))
    neuron_vars         = get_vars_with_prop(sym_type.extern_neuron_variable)
    read_ion_vars       = get_vars_with_prop(sym_type.read_ion_var)
    write_ion_vars      = get_vars_with_prop(sym_type.write_ion_var)
    nonspecific_vars    = get_vars_with_prop(sym_type.nonspecific_cur_var)
    electrode_cur_vars  = get_vars_with_prop(sym_type.electrode_cur_var)
    range_vars          = get_vars_with_prop(sym_type.range_var)
    global_vars         = get_vars_with_prop(sym_type.global_var)
    parameter_vars      = get_vars_with_prop(sym_type.param_assign)
    assigned_vars       = get_vars_with_prop(sym_type.assigned_definition)
    state_vars          = get_vars_with_prop(sym_type.state_var)
    pointer_vars        = get_vars_with_prop(sym_type.pointer_var) | get_vars_with_prop(sym_type.bbcore_pointer_var)
    functions           = get_vars_with_prop(sym_type.function_block)
    procedures          = get_vars_with_prop(sym_type.procedure_block)
    reaction_vars       = set(STR(x.get_node_name()) for x in lookup(ANT.REACT_VAR_NAME))
    compartment_vars    = set()
    for c in lookup(ANT.COMPARTMENT):
        compartment_vars.update(STR(x.get_node_name()) for x in c.names)
    diffusion_vars = set()
    for d in lookup(ANT.LON_DIFUSE):
        diffusion_vars.update(STR(x.get_node_name()) for x in d.names)
    state_vars = state_vars | reaction_vars | compartment_vars | diffusion_vars
    # Check for array variables and ignore them. They should have already been
    # unrolled into individual variables by now.
    for x in sym_table.get_variables_with_properties(sym_type.assigned_definition):
        node = x.get_node()
        if '[' in str(node):
            assigned_vars.discard(STR(x.get_name()))
    # Find all symbols which are provided by or are visible to the larger NEURON simulation.
    external_vars = (
            neuron_vars |
            read_ion_vars |
            write_ion_vars |
            nonspecific_vars |
            electrode_cur_vars |
            range_vars |
            global_vars |
            state_vars |
            pointer_vars |
            functions |
            procedures |
            verbatim_vars)
    # Find the units associated with each assigned variable.
    assigned_units = {name: '' for name in assigned_vars}
    for stmt in lookup(ANT.ASSIGNED_DEFINITION):
        if stmt.unit:
            assigned_units[STR(stmt.name)] = STR(stmt.unit)
    # Code analysis: determine the read/write usage patterns for each variable.
    rw = RW_Visitor()
    rw.visit_program(AST)
    # Split the document into its top-level blocks for easier manipulation.
    blocks_list = [SimpleNamespace(node=x, text=nmodl.to_nmodl(x)) for x in AST.blocks]
    blocks      = {get_block_name(x.node): x for x in blocks_list}

    # Inline the parameters.
    parameters = {}
    for name in (parameter_vars - external_vars - rw.all_writes - parameter_name_conflicts):
        for node in sym_table.lookup(name).get_nodes():
            if node.is_param_assign() and node.value is not None:
                value = float(STR(node.value))
                units = ('('+STR(node.unit.name)+')') if node.unit else ''
                parameters[name] = (value, units)
                print_verbose(f'inline PARAMETER: {name} = {value} {units}')

    # Inline celsius if it's given and if this nmodl file uses it.
    if celsius is not None and 'celsius' in parameter_vars:
        if 'celsius' in verbatim_vars:
            pass # Can not inline into VERBATIM blocks.
        else:
            # Overwrite any existing default value with the given value.
            parameters['celsius'] = (celsius, '(degC)')
            print_verbose(f'inline PARAMETER: celsius = {celsius} (degC)')

    # Inline Q10. Detect and inline assigned variables with known constant
    # values that are set in the initial block.
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
        global_scope  = dict(nmodl_to_python.nmodl_builtins)
        initial_scope = {}
        # Represent unknown external input values as NaN's.
        for name in external_vars | parameter_vars:
            global_scope[name] = math.nan
        # Only use the parameters which we've committed to hard-coding.
        for name, (value, units) in parameters.items():
            global_scope[name] = value
        # Zero initialize the ASSIGNED and STATE variables.
        for name in assigned_vars | state_vars:
            global_scope[name] = 0.0
        # 
        if can_exec:
            try:
                exec(x.pycode, global_scope, initial_scope)
            except Exception as error:
                pycode = prepend_line_numbers(x.pycode.rstrip())
                print_exception(error)
                print_verbose("warning: could not execute INITIAL block:\n" + pycode)
                initial_scope = {}
        # Find all of the variables which are written to durring the runtime.
        # These variables obviously do not have a constant value.
        runtime_writes_to = set()
        for block_name, variables in rw.writes.items():
            if block_name != 'INITIAL':
                runtime_writes_to.update(variables)
        # Search the local scope of the INITIAL block for variables which can be optimized away.
        for name, value in initial_scope.items():
            if name in assigned_vars:
                if name in external_vars: continue
                if name in runtime_writes_to: continue
                # Filter out values that can not be computed ahead of time
                # because they depends on unknown external values (like the
                # voltage or the cell diameter).
                if math.isnan(value): continue
                # 
                units = assigned_units[name]
                assigned_const_value[name] = (value, units)
                print_verbose(f'inline ASSIGNED with constant value: {name} = {value} {units}')

    # Convert assigned variables into local variables as able.
    assigned_to_local = set(assigned_vars) - set(external_vars) - set(assigned_const_value)
    # Search for variables whose persistent state is ignored/overwritten.
    for block_name, read_variables in rw.reads.items():
        assigned_to_local -= read_variables
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
    substitutions = dict(parameters)
    substitutions.update(assigned_const_value)
    # Delete any references to the substituted symbols out of TABLE statements.
    # First setup a regex to find the TABLE statements.
    list_regex  = r'\w+(\s*,\s*\w+)*'
    table_regex = rf'\bTABLE\s+(?P<table_vars>{list_regex}\s+)?(DEPEND\s+(?P<depend_vars>{list_regex})\s+)?FROM\b'
    table_regex = re.compile(table_regex)
    def rewrite_table_stmt(match):
        match = match.groupdict()
        # Process each list of variables and store them back into the dict.
        for section in ('table_vars', 'depend_vars'):
            var_list = match[section]
            if var_list is None:
                var_list = ''
            else:
                var_list = re.split(r'\s+|,', var_list)
                var_list = [x for x in var_list if x] # Filter out empty strings.
                var_list = [x for x in var_list if x not in substitutions] # Filter out hardcoded parameters.
                var_list = [x for x in var_list if x not in assigned_to_local] # Filter out local vars with no persistent state.
                var_list = ', '.join(var_list)
            match[section] = var_list
        # Rewrite the TABLE statement using the new lists of variables.
        table_vars  = match['table_vars']
        depend_vars = match['depend_vars']
        if depend_vars:
            return f'TABLE {table_vars} DEPEND {depend_vars} FROM'
        else:
            return f'TABLE {table_vars} FROM'
    # Search for the blocks which contain code.
    for block in blocks_list:
        if block.node.is_model(): continue
        if block.node.is_block_comment(): continue
        if block.node.is_neuron_block(): continue
        if block.node.is_unit_block(): continue
        if block.node.is_unit_state(): continue
        if block.node.is_param_block(): continue
        if block.node.is_state_block(): continue
        if block.node.is_assigned_block(): continue
        if block.node.is_local_list_statement(): continue
        if block.node.is_define(): continue
        # 
        block.text = re.sub(table_regex, rewrite_table_stmt, block.text)
        # Don't substitute function/procedure arguments.
        declaration, brace, body = block.text.partition('{')
        if not brace:
            body = declaration
            declaration = ''
        # 
        for name, (value, units) in substitutions.items():
            # The assignment to this variable is still present, it's just
            # converted to a local variable. The compiler should be able to
            # eliminate the dead/unused code.
            if block.node.is_initial_block() and name in assigned_const_value:
                continue
            # Some NMODL statements care about int vs float, so don't cast integers to float.
            if float(value) == int(value):
                value = int(value)
            # Substitute the symbol out of general code.
            value = str(value) + units
            body  = re.sub(rf'\b{name}\b', value, body)
        block.text = declaration + brace + body

    # Check the temperature in the INITIAL block.
    if 'celsius' in parameters:
        if block := blocks.get('INITIAL', None):
            signature, start, body = block.text.partition('{')
            check_temp = f"\n    VERBATIM\n    assert(celsius == {parameters['celsius'][0]});\n    ENDVERBATIM\n"
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
            x.node.is_model() or x.node.is_block_comment() or
            x.node.is_local_list_statement() or x.node.is_define()))

    # Join the top-level blocks back into one big string and save it to the output file.
    nmodl_text = '\n\n'.join(x.text for x in blocks_list) + '\n'

    # Break up very long lines into multiple lines as able.
    nmodl_text = re.sub(r'.{500}\b', lambda m: m.group() + '\n', nmodl_text)

    print_verbose(f'write file: "{output_file}"')
    with output_file.open('w') as f:
        f.write(nmodl_text)

