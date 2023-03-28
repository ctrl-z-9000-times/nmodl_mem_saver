import argparse
import nmodl.ast
import nmodl.dsl
import nmodl.symtab
import numpy as np
import re
from pathlib import Path

"""
TODO docs
"""

# TODO: Add author & copyright strings here.

# TODO: Name this program: "nmodl_mem_saver"

# TODO: Add a verbose mode that prints everything it removes.

# TODO: Accept directories and process entire directories.


parser = argparse.ArgumentParser(prog='lti_sim',
        description="TODO",)

parser.add_argument('input_file', type=str, metavar='INPUT_PATH',
        help="input nmodl filename")

parser.add_argument('output_file', type=str, metavar='OUTPUT_PATH',
        help="output nmodl filename")

# parser.add_argument('-dt', type=float, default=None,
#         help="milliseconds")
parser.add_argument('-c', '--celsius', type=float, default=None,
        help="")

args = parser.parse_args()


def process_file(input_file, output_file):
    input_file  = Path(input_file).absolute()
    output_file = Path(output_file).absolute()
    assert input_file != output_file
    with open(input_file, 'rt') as f:
        nmodl_text = f.read()
    ANT = nmodl.ast.AstNodeType
    AST = nmodl.dsl.NmodlDriver().parse_string(nmodl_text)
    # nmodl.ast.view(AST)             # Useful for debugging.
    # print(AST.get_symbol_table())   # Useful for debugging.
    nmodl.symtab.SymtabVisitor().visit_program(AST)
    nmodl.dsl.visitor.InlineVisitor().visit_program(AST)
    nmodl.symtab.SymtabVisitor().visit_program(AST)
    nmodl_text  = nmodl.dsl.to_nmodl(AST)
    visitor     = nmodl.dsl.visitor.AstLookupVisitor()
    lookup      = lambda n: visitor.lookup(AST, n)
    symtab      = AST.get_symbol_table()
    sym_type    = nmodl.symtab.NmodlType
    parameters  = [x.get_name() for x in symtab.get_variables_with_properties(sym_type.param_assign)]
    range_vars  = [x.get_name() for x in symtab.get_variables_with_properties(sym_type.range_var)]
    read_ions   = [x.get_name() for x in symtab.get_variables_with_properties(sym_type.read_ion_var)]
    write_ions  = [x.get_name() for x in symtab.get_variables_with_properties(sym_type.write_ion_var)]
    assigned    = [x.get_name() for x in symtab.get_variables_with_properties(sym_type.assigned_definition)]
    state_vars  = [x.get_name() for x in symtab.get_variables_with_properties(sym_type.state_var)]
    neuron_vars = [x.get_name() for x in symtab.get_variables_with_properties(sym_type.extern_neuron_variable)]
    functions   = [x.get_name() for x in symtab.get_variables_with_properties(sym_type.function_block)]
    procedures  = [x.get_name() for x in symtab.get_variables_with_properties(sym_type.procedure_block)]
    initial_block       = lookup(ANT.INITIAL_BLOCK)
    breakpoint_block    = lookup(ANT.BREAKPOINT_BLOCK)
    derivative_block    = lookup(ANT.DERIVATIVE_BLOCK)
    kinetic_block       = lookup(ANT.KINETIC_BLOCK)
    linear_block        = lookup(ANT.LINEAR_BLOCK)
    non_linear_block    = lookup(ANT.NON_LINEAR_BLOCK)
    solver_blocks       = [str(x.block_name) for x in lookup(ANT.SOLVE_BLOCK)]

    def find_next_closing_brace(text):
        """ Find the next closing curly brace, accounting for any nested blocks. """
        depth = 0
        for brace in re.finditer(r'{|}', text):
            if brace.group() == '{':
                depth += 1
            elif brace.group() == '}':
                depth -= 1
            if depth == 0:
                break
        return brace.end()

    # Remove all FUNCTION and PROCEDURE blocks because they've been inlined.
    # Except if they're the target of a SOLVE statement.
    remove_blocks = functions + procedures
    remove_blocks = [x for x in remove_blocks if x not in solver_blocks]
    for name in remove_blocks:
        match = re.search(rf'(FUNCTION|PROCEDURE)\s+{name}\b', nmodl_text)
        assert match
        head, tail = nmodl_text[:match.start()], nmodl_text[match.end():]
        end_of_block = find_next_closing_brace(tail)
        nmodl_text = head + tail[end_of_block + 1:]

    # Inline all of the parameters.
    replace_parameters = {}
    for name in parameters:
        if name in range_vars: continue
        if name in read_ions: continue
        if name in write_ions: continue
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
    # First remove the inlined parameters from the PARAMETERS block.
    old_param_block = lookup(ANT.PARAM_BLOCK)[0]
    new_param_block = []
    for stmt in old_param_block.statements:
        if stmt.is_param_assign():
            if str(stmt.name) in replace_parameters:
                continue
        stmt_nmodl = nmodl.dsl.to_nmodl(stmt)
        new_param_block.append(stmt_nmodl)
    new_param_block = '\n'.join('    ' + x for x in new_param_block)
    new_param_block = 'PARAMETER {\n' + new_param_block + '\n}\n'
    nmodl_text = re.sub(r'PARAMETER\s*{[^}]*}', new_param_block, nmodl_text)
    # Then substitute the parameters with their values.
    for name, value in replace_parameters.items():
        nmodl_text = re.sub(rf'\b{name}\b', str(value), nmodl_text)

    # Inline Q10.
    pass

    # Convert assigned variables into local variables.
    pass

        # Search for variables that are:
        #       assigned to without first being read,
        #       
        # Ok, lets sort out all of the symbols in the code block...
        #   -> arguments: read, never written.
        #   -> outputs: never read, written.
        #   -> global variables: both read and written.
        #   -> local variables: written and then read.
        # Determine this info for all three blocks: init, breakpoint, solve
        # 



    with output_file.open('w') as f:
        f.write(nmodl_text)



process_file(args.input_file, args.output_file)

