#
# (c) 2018, Tobias Kohn
#
# Created: 15.08.2018
# Updated: 29.08.2018
#
# License: Apache 2.0
#
import ast
from . import pyma_ast
from . import pattern_parser


class Compiler(ast.NodeVisitor):
    """

    """

    def __init__(self, filename: str, source_text: str):
        self.filename = filename
        self.source_text = source_text
        self.methods = []
        self.alternative_lock = 0
        self.sources = []
        self.targets = []
        self._parser = pattern_parser.PatternParser(self.filename, self.source_text)

    def _syntax_error(self, msg: str, node: ast.AST):
        if hasattr(node, 'lineno') and hasattr(node, 'col_offset'):
            line = self.source_text.split('\n')[node.lineno-1] if self.source_text is not None else None
            return SyntaxError(msg, (self.filename, node.lineno, node.col_offset, line))
        else:
            return SyntaxError(msg)

    def create_class(self, node, name: str, guard: str):
        node = self._parser.parse(node)
        self.methods = []
        self.alternative_lock = 0
        self.sources = []
        self.targets = []
        cond = self.visit(node).format('node')
        self.targets.sort()
        result = [
            f"class {name}(CaseManager):",
            self._create_init(),
            self._create_enter(),
            self._create_guard(guard),
            self._create_test(cond, None if guard is None else 'self.test_guard()'),
        ]
        result += self.methods
        return '\n\n'.join(result) + '\n'

    def get_targets(self):
        return self.targets

    def _create_enter(self):
        result = "\tdef __enter__(self):\n" \
                 "\t\tif self._handled:\n" \
                 "\t\t\tself._guard = False\n" \
                 "\t\telse:\n" \
                 "\t\t\tself._guard = self.test(self._value)\n" \
                 "\t\tt = self.targets\n"
        if len(self.targets) > 0:
            targets = ', '.join(["t['{}']".format(name) for name in self.targets])
            result += f"\t\treturn self._guard, {targets}"
        else:
            result += "\t\treturn self._guard"
        return result

    def _create_guard(self, guard: str):
        if guard is None:
            code = "\tdef test_guard(self):\n" \
                   "\t\treturn True\n"
            return code

        code = ["\tdef test_guard(self):"]
        self_name = 'self'
        while self_name in self.sources:
            self_name = '_' + self_name
        if self_name != 'self':
            code.append(f"\t\t{self_name} = self")
        for src in self.sources:
            code.append(f"\t\t{src} = {self_name}.source['{src}']")
        for target in self.targets:
            code.append(f"\t\t{target} = {self_name}.targets['{target}']")
        code.append(f"\t\treturn {guard}")
        return '\n'.join(code)

    def _create_init(self):
        targets = ', '.join([" '{}': None".format(name) for name in self.targets])
        result = "\tdef __init__(self, value, **source):\n" \
                 "\t\tsuper().__init__(value)\n" \
                 "\t\tself.source = source\n" \
                 "\t\tself.targets = {" + targets + " }"
        return result

    def _create_test(self, cond: str, guard: str):
        result = f"\tdef test(self, node):\n" \
                 f"\t\tresult = {cond}\n"
        if guard is not None and guard != '':
            result += f"\t\tif result and not {guard}:\n" \
                      "\t\t\treturn False\n"
        result += "\t\treturn result"
        return result

    def check_target(self, target: str, node: ast.AST):
        if self.alternative_lock > 0:
            raise self._syntax_error("name bindings are not allowed inside alternative branches", node)
        if target in self.targets:
            raise self._syntax_error(f"redefinition of name {target}", node)
        self.targets.append(target)

    def make_method(self, code):
        name = f"_test_{len(self.methods)}"
        method = f"\tdef {name}(self, node):\n" \
                 f"\t\t" + '\n\t\t'.join(code)
        self.methods.append(method)
        return f"self.{name}({{}})"

    def use_name(self, name):
        if type(name) is str:
            if '.' in name:
                idx = name.index('.')
                base = self.use_name(name[:idx])
                return base + name[idx:]
            else:
                if name not in self.sources:
                    self.sources.append(name)
                return f"self.source['{name}']"
        elif type(name) in (list, set, tuple):
            names = [self.use_name(n) for n in name]
            return '(' + ', '.join(names) + ')'
        else:
            raise SystemError(f"this is not a name: '{name}'")

    def generic_visit(self, node):
        raise SystemError(f"unexpected node in pattern matching: '{ast.dump(node)}'")

    def visit_Alternatives(self, node: pyma_ast.Alternatives):
        if all(isinstance(elt, pyma_ast.Constant) for elt in node.elts):
            return f"{{}} in ({', '.join([repr(elt.value) for elt in node.elts])})"

        code = []
        if all(isinstance(elt, (pyma_ast.AttributeDeconstructor, pyma_ast.Deconstructor)) for elt in node.elts):
            names = set()
            for elt in node.elts:
                if type(elt.name) is str:
                    names.add(elt.name)
                else:
                    for n in elt.name:
                        names.add(n)

            test = f"isinstance({{}}, {self.use_name(names)})"
            if all(isinstance(elt, pyma_ast.Deconstructor) and len(elt.args) == 0 for elt in node.elts):
                return test
            code.append(f"if not {test.format('node')}: return False")

        self.alternative_lock += 1
        for elt in node.elts:
            test = self.visit(elt)
            code.append(f"if {test.format('node')}: return True")
        self.alternative_lock -= 1
        return self.make_method(code)

    def visit_AttributeDeconstructor(self, node: pyma_ast.AttributeDeconstructor):
        code = [
            f"if not isinstance(node, {self.use_name(node.name)}): return False",
        ]
        for key in node.args:
            cond = self.visit(node.args[key]).format(key)
            code.append(f"if not hasattr(node, '{key}') or not {cond}: return False")
        code.append("return True")
        return self.make_method(code)

    def visit_Binding(self, node: pyma_ast.Binding):
        self.check_target(node.target, node)
        cond = self.visit(node.value)
        code = [
            f"self.targets['{node.target}'] = node",
            f"return {cond.format('node')}"
        ]
        return self.make_method(code)

    def visit_Constant(self, node: pyma_ast.Constant):
        return f"{{}} == {repr(node.value)}"

    def visit_Deconstructor(self, node: pyma_ast.Deconstructor):
        if len(node.args) == 0:
            return f"(unapply({{}}, {self.use_name(node.name)}) is not None)"

        code = [
            f"u = unapply(node, {self.use_name(node.name)})",
            "if u is None: return False",
            f"if len(u) < {len(node.args)}:",
            f"\traise TypeError(\"unpacking of '{node.name}'-value did not provide enough arguments\")",
        ]
        if len(node.args) == 1:
            arg = node.args[0]
            test = self.visit(arg).format("u[0]")
            code.append(f"return {test}")
        else:
            for i, arg in enumerate(node.args):
                test = self.visit(arg).format(f"u[{i}]")
                if test != "True":
                    code.append(f"if not {test}: return False")
            code.append("return True")
        return self.make_method(code)

    def visit_Dict(self, node: ast.Dict):
        code = []
        for key, value in zip(node.keys, node.values):
            cond = self.visit(value)
            code.append(f"v = node.get({repr(key.value)}, _NO_VALUE_)")
            code.append(f"if v is _NO_VALUE_ or not {cond.format('v')}: return False")
        code.append("return True")
        return self.make_method(code)

    def visit_RegularExpression(self, node: pyma_ast.RegularExpression):
        code = [
            "import re",
            "if not isinstance(node, str): return False",
            f"m = re.fullmatch({repr(node.pattern)}, node)",
            "return m is not None"
        ]
        return self.make_method(code)

    def visit_RegularExprType(self, node: pyma_ast.RegularExprType):
        if node.type_name in ('float', 'int'):
            code = [
                "try:",
                f"\t{node.type_name}(node)",
                "\treturn True",
                "except ValueError:",
                "\treturn False"
            ]
        elif node.type_name == 'bool':
            code = [
                "if isinstance(node, (bool, int)): return True",
                "if isinstance(node, str):",
                "\treturn node.lower() in ('false', 'true')",
                "return False"
            ]
        else:
            code = [
                "if isinstance(node, str):",
                f"\treturn node.is{node.type_name}",
                "else:",
                "\treturn False"
            ]
        return self.make_method(code)

    def visit_SequencePattern(self, node: pyma_ast.SequencePattern):
        code = [ "try:" ]

        if node.exact_length is not None:
            code.append(f"\tif len(node) != {node.exact_length}: return False")
        elif node.min_length is not None:
            code.append(f"\tif len(node) < {node.min_length}: return False")

        for i, elt in enumerate(node.left):
            cond = self.visit(elt).format(f"node[{i}]")
            code.append(f"\tif not {cond}: return False")
        for i, elt in enumerate(node.right):
            cond = self.visit(elt).format(f"node[{-i-1}]")
            code.append(f"\tif not {cond}: return False")

        if len(node.sub_seqs) > 0:
            code.append(f"\ti = {len(node.left)}")
            code.append(f"\tmax_i = len(node) - {len(node.right)}")
            for j, seq in enumerate(node.sub_seqs):
                cond = ' and '.join([self.visit(elt).format("node[i+{}]".format(i)) for i, elt in enumerate(seq)])
                code.append("\ti0 = i")
                code.append("\thas_match = False")
                code.append("\twhile i < max_i:")
                code.append(f"\t\tif {cond}:")
                code.append("\t\t\thas_match = True")
                code.append("\t\t\tbreak")
                code.append("\t\ti += 1")
                code.append("\tif not has_match: return False")
                name = node.targets[j] if j < len(node.targets) else None
                if name is not None:
                    self.check_target(name, node)
                    code.append(f"\tself.targets['{name}'] = node[i0:i]")
                code.append(f"\ti += {len(seq)}")
            if len(node.targets) > len(node.sub_seqs) and node.targets[-1] is not None:
                name = node.targets[-1]
                self.check_target(name, node)
                b = '' if len(node.right) == 0 else -len(node.right)
                code.append(f"\tself.targets['{name}'] = node[i:{b}]")

        elif len(node.targets) == 1 and node.targets[0] is not None:
            self.check_target(node.targets[0], node)
            a = '' if len(node.left) == 0 else len(node.left)
            b = '' if len(node.right) == 0 else -len(node.right)
            code.append(f"\tself.targets['{node.targets[0]}'] = node[{a}:{b}]")

        code.append("\treturn True")
        code.append("except:")
        code.append("\treturn False")
        return self.make_method(code)

    def visit_StringDeconstructor(self, node: pyma_ast.StringDeconstructor):
        raise NotImplementedError("this feature is currently under development and not implemented yet")
        code = ["import re",
                "i = 0",
                "try:"]
        for elt in node.elts:
            cond = self.visit_str(elt).format('node[i:]')
            code.append(f"\t(s, e) = {cond}")
            code.append("\tif s is None or e is None: return False")
        code.append("\treturn True")
        code.append("except:")
        code.append("\treturn False")
        return self.make_method(code)

    def visit_Wildcard(self, node: pyma_ast.Wildcard):
        if node.is_seq:
            raise self._syntax_error("unexpected sequence wildcard", node)
        return "True"


    def visit_str(self, node):
        name = "visit_str_" + node.__class__.__name__
        method = getattr(self, name, None)
        return method(node)

    def visit_str_Alternatives(self, node: pyma_ast.Alternatives):
        code = []
        return self.make_method(code)

    def visit_str_Constant(self, node: pyma_ast.Constant):
        s = node.value
        code = [
            f"idx = node.find({repr(s)})",
            "if idx >= 0:",
            f"\treturn (idx, idx + {len(s)})",
            "else:",
            "\treturn (None, None)"
        ]
        return self.make_method(code)

    def visit_str_RegularExpression(self, node: pyma_ast.RegularExpression):
        code = [
            "import re",
            f"m = re.search({repr(node.pattern)}, node)",
            "return (None, None) if m is None else (m.start(), m.end())",
        ]
        return self.make_method(code)

    def visit_str_RegularExprType(self, node: pyma_ast.RegularExprType):
        pass

    def visit_str_StringDeconstructor(self, node: pyma_ast.StringDeconstructor):
        pass

    def visit_str_Wildcard(self, node: pyma_ast.Wildcard):
        pass

