from sys import stderr
from types import SimpleNamespace
from pathlib import Path
import math
import re
import shutil
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

def optimize_nmodl(input_file, output_file, external_refs, other_nmodl_refs, celsius=None) -> bool:
    # 
    def print(*strings, **kwargs):
        __builtins__['print'](input_file.name+':', *strings, **kwargs)

    # First read the file as binary and discard as much of it as possible, in
    # case it contains invalid utf-8.
    with open(input_file, 'rb') as f:
        nmodl_text = f.read()

    def clean_nmodl(nmodl_text):
        # Remove comments.
        nmodl_text = re.sub(rb'(?s)\bCOMMENT\b.*?\bENDCOMMENT\b', b'', nmodl_text)
        # Remove INDEPENDENT statements because they're unnecessary and the nmodl library does not like them.
        nmodl_text = re.sub(rb'\bINDEPENDENT\b\s*{[^{}]*}', b'', nmodl_text)
        return nmodl_text

    nmodl_text = clean_nmodl(nmodl_text)

    # Substitute INCLUDE statements with the file that they point to.
    include_regex = re.compile(br'\bINCLUDE\s*"(.*)"')
    def include_file(match):
        file = match.groups()[0].decode()
        # TODO: This is supposed to search the environment variable "MODL_INCLUDES".
        for path in (Path.cwd(), input_file.parent,):
            include_file = path.joinpath(file)
            if include_file.exists():
                with open(include_file, 'rb') as f:
                    return f.read()
        raise ValueError(f'file not found {file}')
    nmodl_text = re.sub(include_regex, include_file, nmodl_text)

    nmodl_text = clean_nmodl(nmodl_text)
    nmodl_text = nmodl_text.decode()
    nmodl_text = ''.join(filter((lambda x: x.isprintable() or x.isspace()), nmodl_text))

    # Parse the nmodl file into an AST.
    try:
        AST = nmodl.NmodlDriver().parse_string(nmodl_text)
    except RuntimeError as error:
        print("warning: could not parse file:", str(error))
        shutil.copy(input_file, output_file.parent.joinpath(input_file.name))
        return
    try:
        nmodl.symtab.SymtabVisitor().visit_program(AST)
    except RuntimeError as error:
        print("warning: could not build symbol table:", str(error))
        shutil.copy(input_file, output_file.parent.joinpath(input_file.name))
        return

    visitor = nmodl.dsl.visitor.AstLookupVisitor()
    lookup  = lambda ast_node_type: visitor.lookup(AST, ast_node_type)
    includes = list(lookup(ANT.INCLUDE))



    # Useful for debugging.
    # nmodl.ast.view(AST)
    # print(AST.get_symbol_table())



    # Find all symbols that are referenced in VERBATIM blocks.
    verbatim_vars = set()
    verbatim_length = 0
    for stmt in lookup(ANT.VERBATIM):
        verbatim_text = nmodl.to_nmodl(stmt)
        verbatim_length += len(verbatim_text)
        for symbol in re.finditer(r'[a-zA-Z]\w*', verbatim_text):
            verbatim_vars.add(symbol.group())
    verbatim_vars -= cpp_keywords
    if verbatim_length / len(nmodl_text) > .50:
        print('warning: too much VERBATIM, will not optimize')
        shutil.copy(input_file, output_file.parent.joinpath(input_file.name))
        return
    # Let's get this warning out of the way. As chunks of arbitrary C/C++ code,
    # VERBATIM blocks can not be analysed. Assume that all symbols in VERBATIM
    # blocks are publicly visible and are both read from and written to.
    # Do not attempt to alter the source code inside of VERBATIM blocks.
    if verbatim_vars:
        print('warning: VERBATIM may prevent optimization')

    # Inline all of the functions and procedures
    if not verbatim_vars: # The NMODL library fails to correctly analyze VERBATIM blocks.
        try:
            nmodl.dsl.visitor.InlineVisitor().visit_program(AST)
        except RuntimeError as error:
            print("warning: could not inline all functions and procedures:", str(error))
        else:
            nmodl_text = nmodl.to_nmodl(AST)
        # Reload the modified AST so that the NMODL library starts from a clean state.
        AST    = nmodl.NmodlDriver().parse_string(nmodl_text)
        lookup = lambda ast_node_type: visitor.lookup(AST, ast_node_type)
        nmodl.symtab.SymtabVisitor().visit_program(AST)

    # Find all external references to this mechanism.
    try:
        suffix = '_' + STR(next(iter(lookup(ANT.SUFFIX))).get_node_name())
    except StopIteration:
        suffix = ''
    external_refs = set(external_refs) # Will mutate, don't alter the original version.
    for x in list(external_refs) + list(other_nmodl_refs):
        if x.endswith(suffix):
            external_refs.add(x[:-len(suffix)])

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
    constant_vars       = get_vars_with_prop(sym_type.constant_var)
    parameter_vars      = get_vars_with_prop(sym_type.param_assign)
    assigned_vars       = get_vars_with_prop(sym_type.assigned_definition)
    state_vars          = get_vars_with_prop(sym_type.state_var)
    pointer_vars        = get_vars_with_prop(sym_type.pointer_var) | get_vars_with_prop(sym_type.bbcore_pointer_var)
    function_vars       = get_vars_with_prop(sym_type.function_block)
    procedure_vars      = get_vars_with_prop(sym_type.procedure_block)
    reaction_vars       = set(STR(x.get_node_name()) for x in lookup(ANT.REACT_VAR_NAME))
    compartment_vars    = set()
    for c in lookup(ANT.COMPARTMENT):
        compartment_vars.update(STR(x.get_node_name()) for x in c.names)
    diffusion_vars = set()
    for d in lookup(ANT.LON_DIFUSE):
        diffusion_vars.update(STR(x.get_node_name()) for x in d.names)
    state_vars = state_vars | reaction_vars | compartment_vars | diffusion_vars
    # Find all array variables.
    array_vars = {}
    for x in sym_table.get_variables_with_properties(sym_type.assigned_definition):
        for decl in x.get_nodes():
            if length := getattr(decl, 'length', None):
                array_vars[STR(x.get_name())] = nmodl.to_nmodl(length)
    # Find all symbols which are provided by or are visible to the larger NEURON simulation.
    external_vars = (
            neuron_vars |
            read_ion_vars |
            write_ion_vars |
            nonspecific_vars |
            electrode_cur_vars |
            state_vars |
            pointer_vars |
            ((function_vars | procedure_vars | range_vars | global_vars | parameter_vars) & external_refs) |
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
    # 
    if block := blocks.get('NET_RECEIVE', None):
        for x in visitor.lookup(block.node, ANT.INITIAL_BLOCK):
            blocks['NET_RECEIVE INITIAL'] = SimpleNamespace(node=x, text=nmodl.to_nmodl(x))


    # Useful for debugging.
    if False:
        symbols = {k:v for k,v in locals().items() if k.endswith('_vars')}
        for name, symbols in sorted(symbols.items()):
            if name == 'external_vars': continue
            if symbols: print((name + ':').ljust(20), ', '.join(sorted(symbols)))
        for block in blocks:
            print("Top Level Block:", block)
            reads  = rw.reads.get(block, set())
            writes = rw.writes.get(block, set())
            maybes = rw.maybes.get(block, set()) - writes
            if reads:  print("Read Variables:",  ', '.join(sorted(reads)))
            if writes: print("Always Write Variables:", ', '.join(sorted(writes)))
            if maybes: print("Maybe Write Variables:", ', '.join(sorted(maybes)))



    ############################################################################
    # Determine what to optimize.


    # Inline the parameters.
    parameters = {}
    for name in ((parameter_vars | constant_vars) - external_vars - rw.all_writes - parameter_name_conflicts):
        for node in sym_table.lookup(name).get_nodes():
            if (node.is_param_assign() or node.is_constant_var()) and node.value is not None:
                value = float(STR(node.value))
                units = ('('+STR(node.unit.name)+')') if node.unit else ''
                parameters[name] = (value, units)
                if node.is_param_assign():
                    print(f'inline PARAMETER: {name} = {value} {units}')
                elif node.is_constant_var():
                    print(f'inline CONSTANT: {name} = {value} {units}')
                else:
                    raise RuntimeError(type(node))

    # Inline celsius if it's given and if this nmodl file uses it.
    if celsius is not None and 'celsius' in parameter_vars:
        if 'celsius' in verbatim_vars:
            pass # Can not inline into VERBATIM blocks.
        else:
            # Overwrite any existing default value with the given value.
            parameters['celsius'] = (celsius, '(degC)')
            print(f'inline TEMPERATURE: celsius = {celsius} (degC)')

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
        except nmodl_to_python.ComplexityError as error:
            can_exec = False
            print('warning: complex INITIAL block may prevent optimization:', error.args[0])
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
                print("warning: could not execute INITIAL block:\n" + pycode)
                print("exception:", str(error))
                initial_scope = {}
        # Find all of the variables which are written to during the runtime.
        # These variables obviously do not have a constant value.
        runtime_writes_to = set()
        for block_name, variables in rw.maybes.items():
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
                print(f'inline ASSIGNED with constant value: {name} = {value} {units}')

    # Convert assigned variables into local variables as able.
    assigned_to_local = set(assigned_vars) - set(external_vars) - set(assigned_const_value)
    # Search for variables whose persistent state is ignored/overwritten.
    for block_name, read_variables in rw.reads.items():
        assigned_to_local -= read_variables
    # 
    for name in assigned_to_local:
        print(f'convert from ASSIGNED to LOCAL: {name}')



    ############################################################################
    # Apply the optimizations.


    # Useful for debugging.
    if False:
        # Do not intentionally apply any optimizations.
        parameters = {}
        assigned_const_value = {}
        assigned_to_local = set()
        print("debug mode: will not apply any optimizations")



    # Rewrite the NEURON block without the removed variables.
    if block := blocks.get('NEURON', None):
        new_block = "NEURON {\n"
        for stmt in block.node.statement_block.statements:
            if stmt.is_global() or stmt.is_range():
                variables = [x.get_node_name() for x in stmt.variables]
                variables = [x for x in variables if x not in parameters]
                variables = [x for x in variables if x not in assigned_to_local]
                variables = [x for x in variables if x not in assigned_const_value]
                if not variables:
                    continue
                if   stmt.is_global(): new_block += '    GLOBAL '
                elif stmt.is_range():  new_block += '    RANGE '
                new_block += ', '.join(variables) + '\n'
            else:
                new_block += '    ' + nmodl.to_nmodl(stmt) + '\n'
        new_block += "}\n"
        block.text = new_block

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

    # Insert new LOCAL statements to replace the removed assigned variables.
    new_locals = {} # Maps from block name to set of names of new local variables.
    new_locals['INITIAL'] = set(assigned_const_value.keys())
    for block_name, write_variables in rw.maybes.items():
        new_locals.setdefault(block_name, set()).update(assigned_to_local & write_variables)
    # 
    for block_name, local_names in new_locals.items():
        local_names = sorted(local_names)
        if not local_names:
            continue
        block = blocks[block_name]
        signature, start, body = block.text.partition('{')
        body = textwrap.indent(body, '    ')
        # Format the local variables for printing.
        for idx, name in enumerate(sorted(local_names)):
            if array_size := array_vars.get(name, None):
                local_names[idx] = name + '[' + array_size + ']'
        local_names = ', '.join(local_names)
        block.text = signature + '{\n    LOCAL ' + local_names + '\n    {' + body + '\n}'

    # Substitute the parameters with their values.
    substitutions = dict(parameters)
    substitutions.update(assigned_const_value)

    # Delete any references to the substituted symbols out of TABLE statements.
    # First setup a regex to find the TABLE statements.
    list_regex  = r'\w+(\s*,\s*\w+)*'
    table_regex = rf'\bTABLE\s+(?P<table_vars>{list_regex}\s+)?(DEPEND\s+(?P<depend_vars>{list_regex})\s+)?FROM\b(?P<tail>.*)'
    table_regex = re.compile(table_regex)
    def rewrite_table_stmt(match):
        match = match.groupdict()
        is_function_table = bool(match['table_vars'])
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
        if table_vars or not is_function_table:
            if depend_vars:
                return f'TABLE {table_vars} DEPEND {depend_vars} FROM' + match['tail']
            else:
                return f'TABLE {table_vars} FROM' + match['tail']
    # Search for the blocks which contain code.
    for block in blocks.values():
        if block.node.is_model(): continue
        if block.node.is_block_comment(): continue
        if block.node.is_neuron_block(): continue
        if block.node.is_unit_block(): continue
        if block.node.is_unit_state(): continue
        if block.node.is_param_block(): continue
        if block.node.is_constant_block(): continue
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

    # Special case for initial block inside of net receive.
    if initial_block := blocks.get('NET_RECEIVE INITIAL', None):
        block = blocks['NET_RECEIVE']
        before, keyword, after = block.text.partition('INITIAL')
        # Discard the next block of statements.
        depth = 0
        for match in re.finditer(r'{|}', after):
            if match.group() == '{':
                depth += 1
            elif match.group() == '}':
                depth -= 1
                if depth == 0:
                    break
        assert (depth == 0) and (match.group() == '}')
        block.text = before + initial_block.text + after[match.end():]

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

    with output_file.open('w') as f:
        f.write(nmodl_text)

