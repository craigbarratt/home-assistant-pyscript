"""Python interpreter for pyscript"""

import ast
import asyncio
import importlib
import logging
import sys
import traceback

import homeassistant.components.pyscript.state as state
import homeassistant.components.pyscript.handler as handler


_LOGGER = logging.getLogger(__name__)

#
# Built-in functions available.  Certain functions are excluded
# to avoid potential security issues.
#
builtinFuncs = {
    "abs":               lambda *arg, **kw: abs(*arg, **kw),
    "all":               lambda *arg, **kw: all(*arg, **kw),
    "any":               lambda *arg, **kw: any(*arg, **kw),
    "ascii":             lambda *arg, **kw: ascii(*arg, **kw),
    "bin":               lambda *arg, **kw: bin(*arg, **kw),
    "bool":              lambda *arg, **kw: bool(*arg, **kw),
    "bytearray":         lambda *arg, **kw: bytearray(*arg, **kw),
    "bytearray.fromhex": lambda *arg, **kw: bytearray.fromhex(*arg, **kw),
    "bytes":             lambda *arg, **kw: bytes(*arg, **kw),
    "bytes.fromhex":     lambda *arg, **kw: bytes.fromhex(*arg, **kw),
    "callable":          lambda *arg, **kw: callable(*arg, **kw),
    "chr":               lambda *arg, **kw: chr(*arg, **kw),
    "complex":           lambda *arg, **kw: complex(*arg, **kw),
    "dict":              lambda *arg, **kw: dict(*arg, **kw),
    "divmod":            lambda *arg, **kw: divmod(*arg, **kw),
    "enumerate":         lambda *arg, **kw: enumerate(*arg, **kw),
    "filter":            lambda *arg, **kw: filter(*arg, **kw),
    "float":             lambda *arg, **kw: float(*arg, **kw),
    "format":            lambda *arg, **kw: format(*arg, **kw),
    "frozenset":         lambda *arg, **kw: frozenset(*arg, **kw),
    "hash":              lambda *arg, **kw: hash(*arg, **kw),
    "hex":               lambda *arg, **kw: hex(*arg, **kw),
    "int":               lambda *arg, **kw: int(*arg, **kw),
    "isinstance":        lambda *arg, **kw: isinstance(*arg, **kw),
    "issubclass":        lambda *arg, **kw: issubclass(*arg, **kw),
    "iter":              lambda *arg, **kw: iter(*arg, **kw),
    "len":               lambda *arg, **kw: len(*arg, **kw),
    "list":              lambda *arg, **kw: list(*arg, **kw),
    "map":               lambda *arg, **kw: map(*arg, **kw),
    "max":               lambda *arg, **kw: max(*arg, **kw),
    "min":               lambda *arg, **kw: min(*arg, **kw),
    "next":              lambda *arg, **kw: next(*arg, **kw),
    "oct":               lambda *arg, **kw: oct(*arg, **kw),
    "ord":               lambda *arg, **kw: ord(*arg, **kw),
    "pow":               lambda *arg, **kw: pow(*arg, **kw),
    "range":             lambda *arg, **kw: range(*arg, **kw),
    "repr":              lambda *arg, **kw: repr(*arg, **kw),
    "reversed":          lambda *arg, **kw: reversed(*arg, **kw),
    "round":             lambda *arg, **kw: round(*arg, **kw),
    "set":               lambda *arg, **kw: set(*arg, **kw),
    "slice":             lambda *arg, **kw: slice(*arg, **kw),
    "sorted":            lambda *arg, **kw: sorted(*arg, **kw),
    "str":               lambda *arg, **kw: str(*arg, **kw),
    "sum":               lambda *arg, **kw: sum(*arg, **kw),
    "tuple":             lambda *arg, **kw: tuple(*arg, **kw),
    "type":              lambda *arg, **kw: type(*arg, **kw),
    "zip":               lambda *arg, **kw: zip(*arg, **kw),
}


allowedImports = set([
    "cmath",
    "datetime",
    "decimal",
    "fractions",
    "homeassistant.const",
    "math",
    "number",
    "random",
    "re",
    "statistics",
    "string",
    "time",
])


#
# Objects returned by return, break and continue statements that change execution flow
#
class EvalStopFlow():
    """Denotes a statement or action that stops execution flow, eg: return, break etc."""
    pass


class EvalReturn(EvalStopFlow):
    """Return statement."""
    def __init__(self, value):
        self.value = value


class EvalBreak(EvalStopFlow):
    """Break statement."""
    pass


class EvalContinue(EvalStopFlow):
    """Continue statement."""
    pass


class EvalName():
    """Indentifier that hasn't yet been resolved."""
    def __init__(self, name):
        self.name = name


class EvalFunc():
    """Class for a callable pyscript function."""

    def __init__(self, a):
        self.a = a
        self.name = a.name
        self.defaults = []
        self.kw_defaults = []
        self.decorators = []
        self.globalNames = set()
        self.nonlocalNames = set()
        self.doc_string = ast.get_docstring(a)

    def getName(self):
        """Return the function name."""
        return self.name

    async def evalDefaults(self, astCtx):
        """Return the function name."""
        self.defaults = []
        for v in self.a.args.defaults:
            self.defaults.append(await astCtx._eval(v))
        self.numPosnArg = len(self.a.args.args) - len(self.defaults)
        self.kw_defaults = []
        for v in self.a.args.kw_defaults:
            self.kw_defaults.append({
                "ok": True if v else False,
                "val": None if not v else await astCtx._eval(v)
            })

    async def evalDecorators(self, astCtx):
        """Evaluate the function decorators arguments."""
        self.decorators = []
        for d in self.a.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Name):
                args = []
                for arg in d.args:
                    args.append(await astCtx._eval(arg))
                # log(f"eval function {self.name} adding decorator {d.func.id}{args}")
                self.decorators.append([d.func.id, args])
            elif isinstance(d, ast.Name):
                # log(f"eval function {self.name} adding decorator {d.id}")
                self.decorators.append([d.id, None])
            else:
                _LOGGER.error(f"function {self.name} has unexpected decorator type {d}")

    def getDecorators(self):
        """Return the function decorators."""
        return self.decorators

    def getDocString(self):
        """Return the function doc_string."""
        return self.doc_string

    def getPositionalArgs(self):
        """Return the function positional arguments."""
        args = []
        for arg in self.a.args.args:
            args.append(arg.arg)
        return args

    async def call(self, astCtx, args=[], kwargs={}):
        """Call the function with the given context and arguments."""
        symTable = {}
        kwargs = kwargs.copy() if kwargs else {}
        for i in range(len(self.a.args.args)):
            varName = self.a.args.args[i].arg
            val = None
            if i < len(args):
                val = args[i]
                if varName in kwargs:
                    raise TypeError("{self.name}() got multiple values for argument '{varName}'")
            elif varName in kwargs:
                val = kwargs[varName]
                del kwargs[varName]
            elif self.numPosnArg <= i < len(self.defaults) + self.numPosnArg:
                val = self.defaults[i-self.numPosnArg]
            else:
                raise TypeError(f"{self.name}() missing {self.numPosnArg - i} required positional arguments")
            # log(f"eval {self.name}: setting arg {varName} = {val}")
            symTable[varName] = val
        for i in range(len(self.a.args.kwonlyargs)):
            varName = self.a.args.kwonlyargs[i].arg
            if varName in kwargs:
                val = kwargs[varName]
                del kwargs[varName]
            elif i < len(self.kw_defaults) and self.kw_defaults[i]["ok"]:
                val = self.kw_defaults[i]["val"]
            else:
                raise TypeError(f"{self.name}() missing required keyword-only arguments")
            # log(f"eval {self.name}: setting arg {varName} = {val}")
            symTable[varName] = val
        if self.a.args.kwarg:
            # log(f"eval {self.name}: setting kwarg = {kwargs}")
            symTable[self.a.args.kwarg.arg] = kwargs
        if self.a.args.vararg:
            if len(args) > len(self.a.args.args):
                symTable[self.a.args.vararg.arg] = tuple(args[len(self.a.args.args):])
            else:
                symTable[self.a.args.vararg.arg] = ()
            # log(f"eval {self.name}: setting varargs {self.a.args.vararg.arg} = {symTable[self.a.args.vararg.arg]}")
        elif len(args) > len(self.a.args.args):
            raise TypeError(f"{self.name}() called with too many positional arguments")
        astCtx.symTableStack.append(astCtx.symTable)
        astCtx.symTable = symTable
        prevFunc = astCtx.currFunc
        astCtx.currFunc = self
        for b in self.a.body:
            v = await astCtx._eval(b)
            if isinstance(v, EvalReturn):
                v = v.value
                break
            # return None at end if there isn't a return
            v = None
        astCtx.symTable = astCtx.symTableStack.pop()
        astCtx.currFunc = prevFunc
        return v


class AstEval(object):
    """Python interpreter AST object evaluator."""

    def __init__(self, name, globalSymTable={}):
        self.name = name
        self.globalSymTable = globalSymTable
        self.symTableStack = []
        self.symTable = self.globalSymTable
        self.localSymTable = {}
        self.currFunc = None
        self.filename = ""
        self.exception = None
        self.exceptionLong = None

    async def astNotImplemented(self, a, *args):
        """Raise NotImplementedError exception for unimplemented AST types."""
        astName = 'ast' + a.__class__.__name__
        raise NotImplementedError(f"{self.name}: not implemented ast " + astName)

    async def _eval(self, a, undefinedCheck=True):
        """Internal evaluator calls specific function based on class type."""
        astName = 'ast' + a.__class__.__name__
        try:
            func = getattr(self, astName, self.astNotImplemented)
            if asyncio.iscoroutinefunction(func):
                v = await func(a)
            else:
                v = func(a)
            if undefinedCheck and isinstance(v, EvalName):
                raise NameError(f"name '{v.name}' is not defined")
            return v
        except asyncio.CancelledError:
            raise
        except Exception as err:
            funcName = self.currFunc.getName() + "(), " if self.currFunc else ""
            self.exception = f"{err} in {funcName}{self.filename} line {a.lineno} column {a.col_offset}"
            _LOGGER.error(f"{err} in {funcName}{self.filename} line {a.lineno} column {a.col_offset}" + " " + traceback.format_exc(-1))
        return None

    # Statements return NONE, EvalBreak, EvalContinue, EvalReturn
    async def astModule(self, a):
        """Execute astModule - a list of statements."""
        v = None
        for b in a.body:
            v = await self._eval(b)
            if isinstance(v, EvalStopFlow):
                return v
        return v

    async def astImport(self, a):
        """Execute import."""
        for imp in a.names:
            if imp.name not in allowedImports:
                raise ModuleNotFoundError(f"import of {imp.name} not allowed")
            if imp.name not in sys.modules:
                mod = importlib.import_module(imp.name)
            else:
                mod = sys.modules[imp.name]
            self.symTable[imp.name if imp.asname is None else imp.asname] = mod

    async def astImportFrom(self, a):
        """Execute from X import Y."""
        if a.module not in allowedImports:
            raise ModuleNotFoundError(f"import from {a.module} not allowed")
        if a.module not in sys.modules:
            mod = importlib.import_module(a.module)
        else:
            mod = sys.modules[a.module]
        for imp in a.names:
            self.symTable[imp.name if imp.asname is None else imp.asname] = getattr(mod, imp.name)

    async def astIf(self, a):
        """Execute if statement."""
        v = None
        if await self._eval(a.test):
            for b in a.body:
                v = await self._eval(b)
                if isinstance(v, EvalStopFlow):
                    return v
        else:
            for b in a.orelse:
                v = await self._eval(b)
                if isinstance(v, EvalStopFlow):
                    return v
        return v

    async def astFor(self, a):
        """Execute for statement."""
        loopVar = await self._eval(a.target)
        r = await self._eval(a.iter)
        for i in r:
            self.symTable[loopVar] = i
            for b in a.body:
                v = await self._eval(b)
                if isinstance(v, EvalStopFlow):
                    break
            if isinstance(v, EvalBreak):
                break
            elif isinstance(v, EvalContinue):
                continue
            elif isinstance(v, EvalReturn):
                return v
        if not isinstance(v, EvalBreak):
            for b in a.orelse:
                v = await self._eval(b)
                if isinstance(v, EvalReturn):
                    return v
        return None

    async def astWhile(self, a):
        """Execute while statement."""
        while 1:
            v = await self._eval(a.test)
            if not v:
                break
            for b in a.body:
                v = await self._eval(b)
                if isinstance(v, EvalStopFlow):
                    break
            if isinstance(v, EvalBreak):
                break
            elif isinstance(v, EvalContinue):
                continue
            elif isinstance(v, EvalReturn):
                return v
        if not isinstance(v, EvalBreak):
            for b in a.orelse:
                v = await self._eval(b)
                if isinstance(v, EvalReturn):
                    return v
        return None

    async def astPass(self, a):
        """Execute pass statement."""
        pass

    async def astExpr(self, a):
        """Execute expression statement."""
        return await self._eval(a.value)

    async def astBreak(self, a):
        """Execute break statement - return special class."""
        return EvalBreak()

    async def astContinue(self, a):
        """Execute continue statement - return special class."""
        return EvalContinue()

    async def astReturn(self, a):
        """Execute return statement - return special class."""
        v = await self._eval(a.value)
        return EvalReturn(v)

    async def astGlobal(self, a):
        """Execute global statement."""
        if self.currFunc:
            for varName in a.names:
                self.currFunc.globalNames.add(varName)

    async def astNonlocal(self, a):
        """Execute nonlocal statement."""
        if self.currFunc:
            for varName in a.names:
                self.currFunc.nonlocalNames.add(varName)

    async def astAssign(self, a):
        """Execute assignment statement."""
        val = await self._eval(a.value)
        for lhs in a.targets:
            if isinstance(lhs, ast.Subscript):
                var = await self._eval(lhs.value)
                if isinstance(lhs.slice, ast.Index):
                    ind = await self._eval(lhs.slice.value)
                    var[ind] = val
                elif isinstance(lhs.slice, ast.Slice):
                    lower = await self._eval(lhs.slice.lower) if lhs.slice.lower else None
                    upper = await self._eval(lhs.slice.upper) if lhs.slice.upper else None
                    step = await self._eval(lhs.slice.step) if lhs.slice.step else None
                    if not lower and not upper and not step:
                        return val
                    elif not lower and not upper and step:
                        var[::step] = val
                    elif not lower and upper and not step:
                        var[:upper] = val
                    elif not lower and upper and step:
                        var[:upper:step] = val
                    elif lower and not upper and not step:
                        var[lower] = val
                    elif lower and not upper and step:
                        var[lower::step] = val
                    elif lower and upper and not step:
                        var[lower:upper] = val
                    else:
                        var[lower:upper:step] = val
            else:
                # log(f"doing eval for assign {ast.dump(lhs)}")
                varName = await self._eval(lhs)
                if varName.find(".") >= 0:
                    state.set(varName,val)
                else:
                    if self.currFunc and varName in self.currFunc.globalNames:
                        self.globalSymTable[varName] = val
                    elif self.currFunc and varName in self.currFunc.nonlocalNames:
                        for symTable in reversed(self.symTableStack):
                            if varName in symTable:
                                symTable[varName] = val
                                break
                        else:
                            raise TypeError("can't find nonlocal '{varName}' for assignment")
                    else:
                        self.symTable[varName] = val

    async def astAugAssign(self, a):
        """Execute augmented assignment statement (eg, lhs += value)"""
        varName = await self._eval(a.target)
        val = await self._eval(ast.BinOp(left=ast.Name(id=varName, ctx=ast.Load()), op=a.op, right=a.value))
        if self.currFunc and varName in self.currFunc.globalNames:
            self.globalSymTable[varName] = val
        elif self.currFunc and varName in self.currFunc.nonlocalNames:
            for symTable in reversed(self.symTableStack):
                if varName in symTable:
                    symTable[varName] = val
                    break
            else:
                raise TypeError("can't find nonlocal '{varName}' for assignment")
        else:
            self.symTable[varName] = val

    async def astDelete(self, a):
        """Execute del statement."""
        for t in a.targets:
            if isinstance(t, ast.Subscript):
                var = await self._eval(t.value)
                if isinstance(t.slice, ast.Index):
                    ind = await self._eval(t.slice.value)
                    for e in ind if isinstance(ind, list) else [ind]:
                        del var[e]
                else:
                    raise NotImplementedError(f"{self.name}: not implemented slice type {t.slice} in del")
            elif isinstance(t, ast.Name):
                if self.currFunc and t.id in self.currFunc.globalNames:
                    if t.id in self.globalSymTable:
                        del self.globalSymTable[t.id]
                elif self.currFunc and t.id in self.currFunc.nonlocalNames:
                    for symTable in reversed(self.symTableStack):
                        if t.id in symTable:
                            del symTable[t.id]
                            break
                elif t.id in self.symTable:
                    del self.symTable[t.id]
                else:
                    raise NameError(f"name '{t.id}' is not defined in del")
            else:
                raise NotImplementedError(f"unknown target type {t} in del")

    def astAttribute2Name(self, a):
        """Combine dotted attributes to allow variable names to have dots."""
        # collapse dotted names, eg:
        #   Attribute(value=Attribute(value=Name(id='i', ctx=Load()), attr='j', ctx=Load()), attr='k', ctx=Store())
        name = a.attr
        val = a.value
        while isinstance(val, ast.Attribute):
            name = val.attr + "." + name
            val = val.value
        if isinstance(val, ast.Name):
            name = val.id + "." + name
        else:
            return None
        return name

    async def astAttribute(self, a):
        """Assemble or apply attributes."""
        fullName = self.astAttribute2Name(a)
        if fullName is not None:
            val = await self.astName(ast.Name(id=fullName, ctx=a.ctx))
        else:
            val = await self._eval(a.value, undefinedCheck=False)
            if isinstance(val, EvalName):
                # logc(logLevel >= 1, f"eval {self.name}: calling astName on {val.name}.{a.attr}")
                return await self.astName(ast.Name(id=f"{val.name}.{a.attr}", ctx=a.ctx))
            return getattr(val, a.attr, None)
        if isinstance(val, EvalName):
            s = fullName.rsplit(".", 1)
            if len(s) == 2:
                val = await self.astName(ast.Name(id=s[0], ctx=a.ctx))
                val = getattr(val, s[1])
        return val

    async def astName(self, a):
        """Identifier looks up value on load, or returns name on set."""
        if isinstance(a.ctx, ast.Load):
            #
            # check other scopes if required by global or nonlocal declarations
            #
            if self.currFunc and a.id in self.currFunc.globalNames:
                if a.id in self.globalSymTable:
                    return self.globalSymTable[a.id]
                else:
                    raise NameError(f"global name '{a.id}' is not defined")
            if self.currFunc and a.id in self.currFunc.nonlocalNames:
                for symTable in reversed(self.symTableStack):
                    if a.id in symTable:
                        return symTable[a.id]
                raise NameError(f"nonlocal name '{a.id}' is not defined")
            #
            # now check in our current symbol table, and then some other places
            #
            if a.id in self.symTable:
                return self.symTable[a.id]
            elif a.id in self.localSymTable:
                return self.localSymTable[a.id]
            elif a.id in self.globalSymTable:
                return self.globalSymTable[a.id]
            elif a.id in builtinFuncs:
                return builtinFuncs[a.id]
            elif handler.get(a.id):
                return handler.get(a.id)
            elif state.exist(a.id):
                return state.get(a.id)
            else:
                #
                # Couldn't find it, so return just the name wrapped in EvalName to
                # distinguish from a string variable value.  This is to support
                # names with ".", which are joined by astAttribute
                #
                return EvalName(a.id)
        else:
            return a.id

    async def astBinOp(self, a):
        """Evaluate binary operators by calling function based on class."""
        astName = 'astBinOp' + a.op.__class__.__name__
        return await getattr(self, astName, self.astNotImplemented)(a.left, a.right)

    async def astBinOpAdd(self, a, b):
        return (await self._eval(a)) + (await self._eval(b))

    async def astBinOpSub(self, a, b):
        return (await self._eval(a)) - (await self._eval(b))

    async def astBinOpMult(self, a, b):
        return (await self._eval(a)) * (await self._eval(b))

    async def astBinOpDiv(self, a, b):
        return (await self._eval(a)) / (await self._eval(b))

    async def astBinOpMod(self, a, b):
        return (await self._eval(a)) % (await self._eval(b))

    async def astBinOpPow(self, a, b):
        return (await self._eval(a)) ** (await self._eval(b))

    async def astBinOpLShift(self, a, b):
        return (await self._eval(a)) << (await self._eval(b))

    async def astBinOpRShift(self, a, b):
        return (await self._eval(a)) >> (await self._eval(b))

    async def astBinOpBitOr(self, a, b):
        return (await self._eval(a)) | (await self._eval(b))

    async def astBinOpBitXor(self, a, b):
        return (await self._eval(a)) ^ (await self._eval(b))

    async def astBinOpBitAnd(self, a, b):
        return (await self._eval(a)) & (await self._eval(b))

    async def astBinOpFloorDiv(self, a, b):
        return (await self._eval(a)) // (await self._eval(b))

    async def astUnaryOp(self, a):
        """Evaluate unary operators by calling function based on class."""
        astName = 'astUnaryOp' + a.op.__class__.__name__
        return await getattr(self, astName, self.astNotImplemented)(a.operand)

    async def astUnaryOpNot(self, a):
        return not (await self._eval(a))

    async def astUnaryOpInvert(self, a):
        return ~(await self._eval(a))

    async def astUnaryOpUAdd(self, a):
        return (await self._eval(a))

    async def astUnaryOpUSub(self, a):
        return -(await self._eval(a))

    async def astCompare(self, a):
        """Evaluate comparison operators by calling function based on class."""
        left = a.left
        for op, right in zip(a.ops, a.comparators):
            astName = 'astCmpOp' + op.__class__.__name__
            val = await getattr(self, astName, self.astNotImplemented)(left, right)
            if not val:
                return False
            left = right
        return True

    async def astCmpOpEq(self, a, b):
        return (await self._eval(a)) == (await self._eval(b))

    async def astCmpOpNotEq(self, a, b):
        return (await self._eval(a)) != (await self._eval(b))

    async def astCmpOpLt(self, a, b):
        return (await self._eval(a)) < (await self._eval(b))

    async def astCmpOpLtE(self, a, b):
        return (await self._eval(a)) <= (await self._eval(b))

    async def astCmpOpGt(self, a, b):
        return (await self._eval(a)) > (await self._eval(b))

    async def astCmpOpGtE(self, a, b):
        return (await self._eval(a)) >= (await self._eval(b))

    async def astCmpOpIs(self, a, b):
        return (await self._eval(a)) is (await self._eval(b))

    async def astCmpOpIsNot(self, a, b):
        return (await self._eval(a)) is not (await self._eval(b))

    async def astCmpOpIn(self, a, b):
        return (await self._eval(a)) in (await self._eval(b))

    async def astCmpOpNotIn(self, a, b):
        return (await self._eval(a)) not in (await self._eval(b))

    async def astBoolOp(self, a):
        """Evaluate boolean operators and and or."""
        if isinstance(a.op, ast.And):
            val = 1
            for arg in a.values:
                v = await self._eval(arg)
                if v == 0:
                    return 0
                else:
                    val = v
            return val
        else:
            for arg in a.values:
                v = await self._eval(arg)
                if v != 0:
                    return v
            return 0

    async def astList(self, a):
        """Evaluate list."""
        if isinstance(a.ctx, ast.Load):
            val = []
            for arg in a.elts:
                v = await self._eval(arg)
                val.append(v)
            return val

    async def astDict(self, a):
        """Evaluate dict."""
        d = {}
        for keyAst, valAst in zip(a.keys, a.values):
            d[await self._eval(keyAst)] = await self._eval(valAst)
        return d

    async def astSet(self, a):
        """Evaluate set."""
        s = set()
        for elt in a.elts:
            s.add(await self._eval(elt))
        return s

    async def astSubscript(self, a):
        """Evaluate subscript."""
        var = await self._eval(a.value)
        if isinstance(a.ctx, ast.Load):
            if isinstance(a.slice, ast.Index):
                slice = await self._eval(a.slice)
                return var[slice]
            elif isinstance(a.slice, ast.Slice):
                lower = (await self._eval(a.slice.lower)) if a.slice.lower else None
                upper = (await self._eval(a.slice.upper)) if a.slice.upper else None
                step = (await self._eval(a.slice.step)) if a.slice.step else None
                if not lower and not upper and not step:
                    return None
                elif not lower and not upper and step:
                    return var[::step]
                elif not lower and upper and not step:
                    return var[:upper]
                elif not lower and upper and step:
                    return var[:upper:step]
                elif lower and not upper and not step:
                    return var[lower]
                elif lower and not upper and step:
                    return var[lower::step]
                elif lower and upper and not step:
                    return var[lower:upper]
                else:
                    return var[lower:upper:step]
        else:
            return None

    async def astIndex(self, a):
        """Evaluate index."""
        return await self._eval(a.value)

    async def astSlice(self, a):
        """Evaluate slice."""
        return await self._eval(a.value)

    async def astCall(self, a):
        """Evaluate function call."""
        func = await self._eval(a.func)
        args = []
        kwargs = {}
        for kw in a.keywords:
            kwargs[kw.arg] = await self._eval(kw.value)
        for arg in a.args:
            args.append(await self._eval(arg))
        argStr = ', '.join(['"'+e+'"' if isinstance(e, str) else str(e) for e in args])

        if isinstance(func, EvalFunc):
            # logc(logLevel >= 3, f"eval {self.name}: calling {func.getName()}({argStr})")
            return await func.call(self, args, kwargs)
        else:
            #
            # try to deduce function name, although this only works in simple cases
            #
            if isinstance(a.func, ast.Name):
                funcName = a.func.id
            elif isinstance(a.func, ast.Attribute):
                funcName = a.func.attr
            else:
                funcName = "<other>"
            if callable(func):
                _LOGGER.debug(f"{self.name}: calling {funcName}({argStr}, {kwargs})")
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)
            else:
                raise NameError(f"function '{funcName}' is not callable (got {func})")

    async def astFunctionDef(self, a):
        """Evaluate function definition."""
        f = EvalFunc(a)
        await f.evalDefaults(self)
        await f.evalDecorators(self)
        self.symTable[f.getName()] = f
        return None

    async def astIfExp(self, a):
        """Evaluate if expression."""
        return await self._eval(a.body) if (await self._eval(a.test)) else await self._eval(a.orelse)

    async def astNum(self, a):
        """Evaluate number."""
        return a.n

    async def astStr(self, a):
        """Evaluate string."""
        return a.s

    async def astNameConstant(self, a):
        """Evaluate name constant."""
        return a.value

    async def astJoinedStr(self, a):
        """Evaluate joined string."""
        s = ""
        for arg in a.values:
            v = await self._eval(arg)
            s = s + str(v)
        return s

    async def astFormattedValue(self, a):
        """Evaluate formatted value."""
        val = await self._eval(a.value)
        if a.format_spec is not None:
            format = await self._eval(a.format_spec)
            return f"{val:{format}}"
        else:
            return f"{val}"

    def astGetNames2Dict(self, a, names={}):
        """Recursively find all the names mentioned in the AST tree."""
        if isinstance(a, ast.Attribute):
            names[self.astAttribute2Name(a)] = 1
        elif isinstance(a, ast.Name):
            names[a.id] = 1
        else:
            for child in ast.iter_child_nodes(a):
                self.astGetNames2Dict(child, names)

    def astGetNames(self):
        """Return list of all the names mentioned in our AST tree."""
        names = {}
        if self.ast:
            self.astGetNames2Dict(self.ast, names)
        return [*names]

    def parse(self, str, filename="<unknown>"):
        """Parse the str source code into an AST tree."""
        self.ast = None
        self.filename = filename
        try:
            if isinstance(str, list):
                str = "\n".join(str)
            self.str = str
            self.ast = ast.parse(str, filename=self.filename)
            return True
        except SyntaxError as err:
            self.exception = f"syntax error {err}"
            self.exceptionLong = traceback.print_exc(-1)
            _LOGGER.error(f"syntax error: {err}")
            return False
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self.exception = f"parsing error {err}"
            self.exceptionLong = traceback.print_exc(-1)
            _LOGGER.error(f"parsing error:" + traceback.format_exc(-1))
            return False

    def getException(self):
        """Return the last exception."""
        return self.exception

    def getExceptionLong(self):
        """Return the last exception in a longer form."""
        return self.exceptionLong

    def setLocalSymTable(self, symTable):
        """Set the local symbol table."""
        self.localSymTable = symTable

    async def eval(self, newStateVars={}):
        """Main entry point to execute code, with the optional state variables added to the scope."""
        self.exception = None
        self.exceptionLong = None
        self.localSymTable.update(newStateVars)
        if self.ast:
            v = await self._eval(self.ast)
            if isinstance(v, EvalStopFlow):
                return None
            return v
        else:
            return None

    def dump(self):
        """Dump the AST tree for debugging."""
        return ast.dump(self.ast)
