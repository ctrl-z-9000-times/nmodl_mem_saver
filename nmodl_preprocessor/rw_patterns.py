import nmodl.dsl
from utils import *

class WriteDetector(nmodl.dsl.visitor.AstVisitor):
    """ Determines which symbols each top-level block writes to. """
    def __init__(self):
        self.current_block = None
        self.writes_to = {} # Maps from block name to set of variable names.
        super().__init__()

    def visit_statement_block(self, node): # Top level code blocks
        self.current_block = get_block_name(node.parent)
        node.visit_children(self)

    def visit_binary_expression(self, node):
        if node.op.eval() == '=':
            name = STR(node.lhs.name.get_node_name())
            self.writes_to.setdefault(self.current_block, set()).add(name)

class OverwriteDetector(nmodl.dsl.visitor.AstVisitor):
    """ Determines which symbols are written to without first being read from. """
    def __init__(self):
        super().__init__()
        # Recored the program wide access patterns for each variable.
        self.read_first  = set()
        self.write_first = set()
        self.variables_seen = set()
        # Also record which blocks each variable is present in.
        self.current_block = None
        self.blocks = {} # Maps from variable name to set of block names.

    def visit_program(self, node):
        node.visit_children(self)

    def visit_statement_block(self, node):
        # Top level code blocks
        if self.current_block is None:
            self.current_block = get_block_name(node.parent)
            self.variables_seen.clear()
            node.accept(self)
            self.current_block = None

    def visit_neuron_block(self, node):
        pass # Does not contain any source code.

    def visit_function_block(self, node):
        pass # Functions are pure and can't access assigned variables.

    def visit_if_statement(self, node):
        node.condition.accept(self)
        blocks = [node.get_statement_block()] + node.elseifs + [node.elses]
        for x in blocks:
            if x is not None:
                inner = OverwriteDetector()
                inner.variables_seen.update(self.variables_seen)
                inner.visit_statement_block(x)
                self.read_first.update(inner.read_first)
                self.write_first.update(inner.write_first)

    def first_access(self, name):
        first_access = name not in self.variables_seen
        if first_access:
            self.variables_seen.add(name)
            self.blocks.setdefault(name, set()).add(self.current_block)
        return first_access

    def visit_binary_expression(self, node):
        if node.op.eval() == '=':
            # Recursively mark all variables on right hand side as being read from.
            node.rhs.accept(self)
            # Mark the left hand side variable of this assignment as being written to.
            name = STR(node.lhs.name.get_node_name())
            if self.first_access(name):
                self.write_first.add(name)
        else:
            super().visit_binary_expression(node)

    def visit_var_name(self, node):
        # Mark this variable as being read from.
        name = STR(node.name.get_node_name())
        if self.first_access(name):
            self.read_first.add(name)

