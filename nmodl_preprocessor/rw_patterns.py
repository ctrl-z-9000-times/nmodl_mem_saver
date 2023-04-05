import nmodl.dsl
from utils import *

class WriteDetector(nmodl.dsl.visitor.AstVisitor):
    """ Determines which symbols each top-level block writes to. """
    def visit_program(self, node):
        self.current_block = None
        self.writes_to = {} # Maps from block name to set of variable names.
        node.visit_children(self)

    def visit_statement_block(self, node): # Top level code blocks
        self.current_block = get_block_name(node.parent)
        node.visit_children(self)

    def visit_binary_expression(self, node):
        if node.op.eval() == '=':
            name = STR(node.lhs.name.get_node_name())
            self.writes_to.setdefault(self.current_block, set()).add(name)

class OverwriteDetector(nmodl.dsl.visitor.AstVisitor):
    """ Determines which symbols are written to without first being read from. """
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
        self.current_block = get_block_name(node.parent)
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

