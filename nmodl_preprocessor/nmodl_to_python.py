"""
Adapted from the tutorial:
https://bluebrain.github.io/nmodl/html/notebooks/nmodl-python-tutorial.html#Easy-code-generation-using-AST-visitors
"""
import textwrap
import nmodl.dsl

# This list of NMODL's built-in functions was copied from the documentation at:
# https://github.com/neuronsimulator/nrn/blob/master/docs/guide/nmodls_built_in_functions.rst
builtin_functions = (
    "abs",
)
builtin_math_functions = (
    "acos",
    "asin",
    "atan",
    "atan2",
    "ceil",
    "cos",
    "cosh",
    "exp",
    "fabs",
    "floor",
    "fmod",
    "log",
    "log10",
    "pow",
    "sin",
    "sinh",
    "sqrt",
    "tan",
    "tanh",
)

class VerbatimError(ValueError): pass

class ComplexityError(ValueError): pass


class PyGenerator(nmodl.dsl.visitor.AstVisitor):
    def __init__(self):
        super().__init__()
        self.code_stack = []
        self.pycode = ""

    def push_block(self):
        self.code_stack.append(self.pycode)
        self.pycode = ""

    def pop_block(self):
        parent_block = self.code_stack.pop()
        if parent_block.rstrip().endswith(':'):
            self.pycode = textwrap.indent(self.pycode, '    ')
        self.pycode = parent_block + self.pycode

    def visit_statement_block(self, node):
        self.push_block()
        node.visit_children(self)
        self.pop_block()

    def visit_expression_statement(self, node):
        node.visit_children(self)
        self.pycode += "\n"

    def visit_wrapped_expression(self, node):
        self.pycode += '('
        node.visit_children(self)
        self.pycode += ')'

    def visit_binary_expression(self, node):
        op = node.op.eval()
        if op == "^":
            op = '**'
        node.lhs.accept(self)
        self.pycode += f" {op} "
        node.rhs.accept(self)

    def visit_var_name(self, node):
        self.pycode += node.name.get_node_name()

    def visit_integer(self, node):
        self.pycode += nmodl.to_nmodl(node)

    def visit_double(self, node):
        self.pycode += nmodl.to_nmodl(node)

    def visit_verbatim(self, node):
        raise VerbatimError()

    def visit_if_statement(self, node):
        self.pycode += "if "
        node.condition.accept(self)
        self.pycode += ":\n"
        node.statement_block.accept(self)
        for elif_node in node.elseifs:
            self.pycode += "elif "
            elif_node.condition.accept(self)
            self.pycode += ":\n"
            elif_node.statement_block.accept(self)
        if else_node := node.elses:
            self.pycode += "else:\n"
            else_node.statement_block.accept(self)

    def visit_function_call(self, node):
        name = node.name.get_node_name()
        if name in builtin_functions:
            pass
        elif name in builtin_math_functions:
            name = f"math.{name}"
        elif name == "net_send":
            raise ComplexityError()
        else:
            # All functions and procedures should have been inlined already.
            # The exceptions mostly involve TABLE statements.
            # I could attempt to evaluate these calls, but that would be very
            # complicated, especially because python does not support ASSIGNED
            # variables, which would need to be communicated back to the caller.
            raise ComplexityError()
        # 
        self.pycode += name + "("
        for arg in node.arguments:
            arg.accept(self)
            self.pycode += ", "
        self.pycode += ")"

    def visit_while_statement(self, node):
        # Can not guarantee correct results BC the condition might reference unknown values.
        raise ComplexityError()

