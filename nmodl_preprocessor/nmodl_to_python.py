"""
Adapted from the tutorial:
https://bluebrain.github.io/nmodl/html/notebooks/nmodl-python-tutorial.html#Easy-code-generation-using-AST-visitors
"""
import textwrap
import nmodl.dsl

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

    def visit_if_statement(self, node):
        self.pycode += "if "
        node.condition.accept(self)
        self.pycode += ":\n"
        node.get_statement_block().accept(self)
        for n in node.elseifs:
            n.accept(self)
        if node.elses:
            node.elses.accept(self)

    def visit_else_statement(self, node):
        self.pycode += "else:\n"
        node.get_statement_block().accept(self)

    def visit_wrapped_expression(self, node):
        self.pycode += '('
        node.visit_children(self)
        self.pycode += ')'

    def visit_binary_expression(self, node):
        lhs = node.lhs
        rhs = node.rhs
        op = node.op.eval()
        if op == "^":
            op = '**'
        lhs.accept(self)
        self.pycode += f" {op} "
        rhs.accept(self)

    def visit_var_name(self, node):
        self.pycode += node.name.get_node_name()

    def visit_integer(self, node):
        self.pycode += nmod.to_nmodl(node)

    def visit_double(self, node):
        self.pycode += nmodl.to_nmodl(node)

