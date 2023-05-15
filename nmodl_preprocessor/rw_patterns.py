import nmodl.dsl
from nmodl_preprocessor.utils import *

class RW_Visitor(nmodl.dsl.visitor.AstVisitor):
    """ Determines which symbols each top-level block reads from and writes to. """
    def __init__(self):
        super().__init__()
        self.current_block = None
        # Maps from block name to set of symbol names.
        self.reads  = {} # Symbols which are read-before-write.
        self.writes = {} # Symbols which are written to (regardless of read status).
        self.maybes = {} # Symbols which are written to (possibly only under certain run-time conditions).
        # Set of all variables which are assigned to.
        self.all_writes = set()

    def visit_program(self, node):
        node.visit_children(self)
        for block_name, var_names in self.writes.items():
            self.all_writes.update(var_names)
        for block_name, var_names in self.maybes.items():
            self.all_writes.update(var_names)
            var_names.update(self.writes[block_name])

    def visit_statement_block(self, node):
        # Look for top level code blocks.
        if self.current_block is None:
            self.current_block = get_block_name(node.parent)
            parameters = set(STR(x.get_node_name()) for x in getattr(node.parent, 'parameters', []))
            self.reads [self.current_block] = parameters
            self.writes[self.current_block] = set()
            self.maybes[self.current_block] = set()
            node.visit_children(self)
            self.current_block = None
        else:
            node.visit_children(self)

    def visit_initial_block(self, node):
        # Special case for initial blocks hiding inside of net receive blocks.
        if self.current_block == 'NET_RECEIVE':
            self.current_block = 'NET_RECEIVE INITIAL'
            self.reads [self.current_block] = set()
            self.writes[self.current_block] = set()
            self.maybes[self.current_block] = set()
            node.visit_children(self)
            self.current_block = 'NET_RECEIVE'
        else:
            node.visit_children(self)

    def visit_reaction_statement(self, node):
        self.visit_diff_eq_expression(node)

    def visit_diff_eq_expression(self, node):
        # The solver may move kinetic equations into different code blocks.
        equation_block = self.current_block
        self.current_block = object()
        self.reads [self.current_block] = set()
        self.writes[self.current_block] = set()
        self.maybes[self.current_block] = set()
        node.visit_children(self)
        self.reads [equation_block] |= self.reads .pop(self.current_block)
        self.writes[equation_block] |= self.writes.pop(self.current_block)
        self.maybes[equation_block] |= self.maybes.pop(self.current_block)
        self.current_block = equation_block

    def visit_binary_expression(self, node):
        if node.op.eval() == '=':
            # Recursively mark all variables on right hand side as being read from.
            node.rhs.accept(self)
            # Mark the left hand side variable of this assignment as being written to.
            name = STR(node.lhs.name.get_node_name())
            self.writes[self.current_block].add(name)
        else:
            node.visit_children(self)

    def visit_var_name(self, node):
        # Mark this variable as being read from, unless its already been
        # overwritten with a new value in this block.
        name = STR(node.name.get_node_name())
        if name not in self.writes[self.current_block]:
            self.reads[self.current_block].add(name)

    def visit_if_statement(self, node):
        node.condition.accept(self)
        for elseif in node.elseifs:
            elseif.condition.accept(self)
        # Collect all of the child blocks that are part of this if-else tree.
        blocks = [node.get_statement_block()] + node.elseifs + [node.elses]
        # Make new visitors and initialize them as copies of our current state.
        for v in (visitors := [RW_Visitor() for branch in blocks]):
            v.current_block              = self.current_block
            v.reads[self.current_block]  = set(self.reads[self.current_block])
            v.writes[self.current_block] = set(self.writes[self.current_block])
            v.maybes[self.current_block] = set()
        # Simultaneously visit all of the arms of the if-else tree.
        for v, branch in zip(visitors, blocks):
            if branch is not None: # Ignore any missing "else" blocks.
                v.visit_statement_block(branch)
        # Collect and combine the results.
        # Any branch reading a symbol counts as a first-time read of the symbol.
        for v in visitors:
            self.reads[self.current_block].update(v.reads[self.current_block])
        # All branches must write a symbol for it to count as being written to.
        # Otherwise it *may be* written to.
        branch_writes = [v.writes[self.current_block] for v in visitors]
        self.writes[self.current_block].update(set.intersection(*branch_writes))
        self.maybes[self.current_block].update(set.union(*branch_writes))
        for v in visitors:
            self.maybes[self.current_block].update(v.maybes[self.current_block])

    def visit_from_statement(self, node):
        name = STR(node.name.get_node_name())
        self.writes[self.current_block].add(name)
        if x := getattr(node, 'from'):            x.accept(self)
        if x := getattr(node, 'to'):              x.accept(self)
        if x := getattr(node, 'increment', None): x.accept(self)
        node.statement_block.accept(self)

    def visit_neuron_block(self, node):
        pass # Does not contain any source code.

