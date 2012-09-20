
# - Include history with each individual
# - Fix problem with extra_caller_vars not being set.
# - Seed according to percents rather than random.
# - Cross over between schedule fragments
# - Put in logic to tell if schedule is not valid in advance to gain some time
# - Can parallelize compile and also improve % valid schedules (for speed)

# - Run my recursive scheduler against PetaBricks
# - Dump top speed and schedule vs generation number out to some number of generations

# Determining schedules
# 1. Autotuning
# 2. Heuristics
#   - Parallelize outer dimension or two, vectorize inner. Instead of doing random splits focus on tiling splits
# 3. Machine learning

# Jonathan+Andrew discussion:
# - Tile size or unroll size needs to be divisible into input size
# - split(x, x, xi, n) is right syntax for now
# - Only vectorize at native vector widths, 2 4 8 16
# - tile() should be in the same order as the variables
# - Should not be 2 vectorize calls
# - Good to have a clamp at the beginning and a dummy function at the end -- autotuner maybe should inject these (optionally), at least the dummy at the end.
   # Could tune over parameters have I clamped at the beginning and have I injected dummy at the end.
   # Smartest thing
#
#  x should be outside y, which is f(y,x,c)
#  transpose(x, y) means push x just far enough to be inside y
#    If split to introduce new variables one becomes two in the same spot. Same with tile, ends up in the order (a,b,c,d) for tile(a,b,c,d).
#  
#  f(y, ..., x)
#  f.transpose(y,x)
#  --> f(...,x,y)
#  foreach y:
#    foreach x:
#        foreach ...:
#permute([defined order list], [desired order list]):
#
# ------------------------------------------------------------------
#
# Autotuner (not using Petabricks)

# Randomly generate size 3

# Connelly TODO research:
# - enumerate valid schedules
# - machine learning schedule at a given point
# - dynamic programming or tree search or iterative algorithm to find initial guess / plausible schedules

# TODO:
# - global creation (for more than one function)
#   - chunk (and all functions actually) should list variables that are created due to split/tile (see e.g. camera_raw).
# - create random schedule as well as enumerate all schedules of a given length
# - transpose -- does it always need to be at the end?

import halide
import random
import collections
import itertools
import permutation
import time
import copy
import numpy
import sys
import os
from ForkedWatchdog import Watchdog
import examples
random_module = random

AUTOTUNE_VERBOSE = False #True #False #True
DEFAULT_MAX_DEPTH = 4
DEFAULT_IMAGE = 'apollo2.png'
DEFAULT_TESTER_KW = {'in_image': DEFAULT_IMAGE}
FORCE_TILE = False
MUTATE_TRIES = 10

# --------------------------------------------------------------------------------------------------------------
# Valid Schedule Enumeration
# --------------------------------------------------------------------------------------------------------------

FUNC_ROOT   = 0
FUNC_INLINE = 1
FUNC_CHUNK  = 2         # Needs a variable in the caller
# Chunk not implemented yet

VAR_SERIAL             = 0
VAR_VECTORIZE          = 1
VAR_PARALLEL           = 2
VAR_UNROLL             = 3
VAR_TILE               = 4
VAR_SPLIT              = 5
# Tile and split not implemented yet (recursion not implemented). Also vectorize() and unroll() implicitly create a new variable so they recurse also.
# transpose() always there or sometimes not there
# To get long schedules should be able to randomize from an existing schedule.
# Also schedules have some global interactions when new variables are introduced so refactor to handle that.

def default_check(cls, L):
    def count(C):
        return sum([isinstance(x, C) for x in L])
    if len(L) == 0:
        return True
    else:
        # Handle singleton fragments
        if FORCE_TILE and (len(L) >= 2 and count(FragmentTile) < 1):
            return False
        if isinstance(L[0], FragmentRoot) and count(FragmentRoot) == 1 and count(FragmentChunk) == 0:
            return True
        elif isinstance(L[0], FragmentChunk) and len(L) == 1:
            return True
    return False
            
class Fragment:
    "Base class for a single schedule macro applied to a Func, e.g. .vectorize(x), .parallel(y), .root(), etc."
    def __init__(self, var=None, value=None):
        self.var = var
        self.value = value
        
    @staticmethod
    def fragments(root_func, func, cls, vars, extra_caller_vars):
        "Given class and variable list (of strings) returns fragments possible at this point."
#        print 'fragments base', cls
        return [cls()]
        
    def ___str__(self):
        "Returns schedule_str, e.g. '.parallel(y)'."
    
    def new_vars(self):
        "List of new variable names, e.g. ['v'] or []."
        return []
        
    def randomize_const(self):
        "Randomize constants e.g. change vectorize(x, 8) => vectorize(x, (random value))."
    
    def check(self, L):
        "Given list of Schedule fragments (applied to a function) returns True if valid else False."
        return default_check(self.__class__, L)

class FragmentVarMixin:
    @staticmethod
    def fragments(root_func, func, cls, vars, extra_caller_vars):
#        print 'fragments', cls
        return [cls(x) for x in vars]

use_random_blocksize = True

def blocksize_random(L=None, force_random=False):
    if L is None:
        L = [2,4,8,16,32,64]
    return random.choice(L) #if (use_random_blocksize and not force_random) else 3

class FragmentBlocksizeMixin(FragmentVarMixin):
    def __init__(self, var=None, value=None):
#        print '__init__', self.__class__
        self.var = var
        self.value = value
        if self.value is None:
            self.value = 0

    def randomize_const(self):
        self.value = blocksize_random()
        #print 'randomize_const, value=%d'% self.value

    def check(self, L):
        return check_duplicates(self.__class__, L)

def check_duplicates(cls, L):
    if not default_check(cls, L):
        return False
    #count = collections.defaultdict(lambda: 0)
    #for x in L:
    #    if isinstance(x, cls):
    #        count[x.var] += 1
    #        if count[x.var] >= 2:
    #            return False
    d = set()
    for x in L:
        s = str(x)
        if s in d:
            return False
        d.add(s)
        
    return True

class FragmentRoot(Fragment):
    def __str__(self):
        return '.root()'

class FragmentVectorize(FragmentBlocksizeMixin,Fragment):
    def randomize_const(self):
        self.value = blocksize_random([2,4,8,16])

    def __str__(self):
        return '.vectorize(%s,%d)'%(self.var, self.value) #self.value) # FIXMEFIXME Generate random platform valid blocksize
    
class FragmentParallel(FragmentVarMixin,Fragment):
    def __str__(self):
        return '.parallel(%s)'%(self.var)

class FragmentUnroll(FragmentBlocksizeMixin,Fragment):
    def __str__(self):
        return '.unroll(%s,%d)'%(self.var,self.value)

class FragmentChunk(Fragment):
    @staticmethod
    def fragments(root_func, func, cls, vars, extra_caller_vars):
        return [cls(x) for x in caller_vars(root_func, func)+extra_caller_vars]
        
    def check(self, L):
        return check_duplicates(self.__class__, L)

    def __str__(self):
        return '.chunk(%s)'%self.var

def create_var(vars): #count=[0]):
    #count[0] += 1
    for i in itertools.count(0):
        s = '_c%d'%i#count[0]
        if not s in vars:
            return s

def instantiate_var(name, cache={}):
    if name in cache:
        return cache[name]
    cache[name] = halide.Var(name)
    return cache[name]

# split(x, x, xi, n)
class FragmentSplit(FragmentBlocksizeMixin,Fragment):
    def __init__(self, var=None, value=None, newvar=None, reuse_outer=False,vars=None):
        FragmentBlocksizeMixin.__init__(self, var, value)
        self.newvar = newvar
        if self.newvar is None:
            self.newvar = create_var(vars)
        self.reuse_outer = reuse_outer
        
    @staticmethod
    def fragments(root_func, func, cls, vars, extra_caller_vars):
        return [cls(x,reuse_outer=True,vars=vars)  for x in vars]
        #([cls(x,reuse_outer=False,vars=vars) for x in vars] +
        #        [cls(x,reuse_outer=True,vars=vars)  for x in vars])

    def new_vars(self):
        return [self.newvar]
    
    def __str__(self):
        return '.split(%s,%s,%s,%d)'%(self.var,self.var if     self.reuse_outer else self.newvar,
                                               self.var if not self.reuse_outer else self.newvar, self.value)

class FragmentTile(FragmentBlocksizeMixin,Fragment):
    def __init__(self, xvar=None, yvar=None, newvar=None, vars=None):
        self.xvar=xvar
        self.yvar=yvar
        self.xsize = 0
        self.ysize = 0
        self.xnewvar = create_var(vars)
        self.ynewvar = create_var(vars+[self.xnewvar])

    def randomize_const(self):
        self.xsize = blocksize_random()
        self.ysize = blocksize_random()
        #print 'randomize_const, tile, size=%d,%d' % (self.xsize, self.ysize)

    def check(self, L):
        return check_duplicates(self.__class__, L)

    @staticmethod
    def fragments(root_func, func, cls, vars, extra_caller_vars):
        ans = []
        for i in range(len(vars)):
            for j in range(i+1, len(vars)):
                ans.append(cls(vars[i],vars[j],vars=vars))
        return ans
#        return [cls(x,y,vars=vars) for x in vars for y in vars if x != y]

    def new_vars(self):
        return [self.xnewvar, self.ynewvar]
    
    def __str__(self):
        return '.tile(%s,%s,%s,%s,%d,%d)'%(self.xvar,self.yvar,self.xnewvar,self.ynewvar,self.xsize,self.ysize)

class FragmentTranspose(Fragment):
    # Actually makes potentially many calls to transpose, but is considered as one fragment
    def __init__(self, vars, idx):
        self.vars = vars
        self.idx = idx
        self.permutation = permutation.permutation(vars, idx)
        self.pairwise_swaps = permutation.pairwise_swaps(vars, self.permutation)
    
    @staticmethod
    def fragments(root_func, func, cls, vars, extra_caller_vars):
        return [cls(vars=vars, idx=i) for i in range(1,permutation.factorial(len(vars)))]     # TODO: Allow random generation so as to not loop over n!
    
    def check(self, L):
        if not default_check(self.__class__, L):
            return False
        return [isinstance(x, FragmentTranspose) for x in L] == [0]*(len(L)-1)+[1]
    
    def __str__(self):
        ans = ''
        assert len(self.pairwise_swaps) >= 1
        for (a, b) in self.pairwise_swaps:
            ans += '.transpose(%s,%s)'%(a,b)
        return ans

fragment_classes = [FragmentRoot, FragmentVectorize, FragmentParallel, FragmentUnroll, FragmentChunk, FragmentSplit, FragmentTile, FragmentTranspose]

class MutateFailed(Exception):
    "Mutation can fail due to e.g. trying to add a fragment to f.chunk(c), which is a singleton schedule macro."
    
class FragmentList(list):
    "A list of schedule macros applied to a Func, e.g. f.root().vectorize(x).parallel(y)."
    def __init__(self, func, L):
        self.func = func
        list.__init__(self, L)
        
    def __str__(self):
        #print '__str__', list(self)
        ans = []
        for x in self:
            #print 'str of', x
            #print 'next'
            ans.append(str(x))
        if len(ans):
            #print 'returning list'
            return self.func.name() + ''.join(ans)
        #print 'returning empty'
        return ''

    def new_vars(self):
        ans = []
        for x in self:
            ans.extend(x.new_vars())
        return list(sorted(set(ans)))
        
    def __repr__(self):
        return "'" + str(self) + "'" #return 'FragmentList(%s, %r)' % (self.func, repr([str(x) for x in list(self)]))
        
    def randomized_const(self):
        ans = FragmentList(self.func, [copy.copy(x) for x in self])
        for x in ans:
            x.randomize_const()
        #    print '->', x
        #print 'FragmentList, after randomization:'
        #print ans
        return ans
    
    def check(self):
        for x in self:
            if not x.check(self):
                return False
        return True

    def added_or_edited(self, root_func, extra_caller_vars, vars=None, delta=0):
        if vars is None:
            vars = halide.func_varlist(self.func)
        for j in range(MUTATE_TRIES):
            L = copy.copy(list(self))
            i = random.randrange(len(L)+1-delta)
            all_vars = list(vars)
            for fragment in L[:i]:
                all_vars.extend(fragment.new_vars())
            L[i:i+delta] = [random_fragment(root_func, self.func, all_vars, extra_caller_vars)]
            ans = FragmentList(self.func, L)
#            print ans, ans.check()
            if ans.check():
#                print '-'*40
                return ans
        raise MutateFailed

    def added(self, root_func, extra_caller_vars, vars=None):
        "Copy of current FragmentList that checks and has a single fragment added."
        ans = self.added_or_edited(root_func, extra_caller_vars, vars, delta=0)
        assert len(ans) == len(self)+1
        return ans
        
    def removed(self):
        "Copy of current FragmentList that checks and has a single fragment removed."
        for j in range(MUTATE_TRIES):
            L = copy.copy(list(self))
            i = random.randrange(len(L))
            del L[i:i+1]
            ans = FragmentList(self.func, L)
            if ans.check():
                return ans
        raise MutateFailed

    def edited(self, root_func, extra_caller_vars, vars=None):
        "Copy of current FragmentList that checks and has a single fragment edited."
        ans = self.added_or_edited(root_func, extra_caller_vars, vars, delta=1)
        assert len(ans) == len(self)
        return ans

def random_fragment(root_func, func, all_vars, extra_caller_vars):
    while True:
        cls = random.choice(fragment_classes)
        fragments = cls.fragments(root_func, func, cls, all_vars, extra_caller_vars)
        if len(fragments):
            break
#    if len(fragments) == 0:    # empty fragments can happen legitimately for e.g. chunk of the root func
#        raise ValueError(('fragments is empty', cls, all_vars, func.name()))
    fragment = random.choice(fragments)
    return fragment

def schedules_depth(root_func, func, vars, depth=0, random=False, extra_caller_vars=[]):
    "Un-checked schedules (FragmentList instances) of exactly the specified depth for the given Func."
#    print func
#    print vars
    if not random:
        randomized = lambda x: x
    else:
        def randomized(La):
            ans = list(La)
            random_module.shuffle(ans)
            return ans
    assert depth >= 0 and isinstance(depth, (int,long))
    
    if depth == 0:
        yield FragmentList(func, [])
    else:
        for L in schedules_depth(root_func, func, vars, depth-1, random):
            if not L.check():
                continue
            all_vars = list(vars)
            for fragment in L:
                all_vars.extend(fragment.new_vars())
            for cls in randomized(fragment_classes):
                #print 'all_vars', all_vars
                for fragment in randomized(cls.fragments(root_func, func, cls, all_vars, extra_caller_vars)):
                    #print 'fragment', fragment
                #print '=>', fragment
                    #print '*', len(L), L
                    yield FragmentList(func, list(L) + [fragment])

def schedules_func(root_func, func, min_depth=0, max_depth=DEFAULT_MAX_DEPTH, random=False, extra_caller_vars=[], vars=None):
    """
    Generator of valid schedules for a Func, each of which is a FragmentList (e.g. f.root().vectorize(x).parallel(y)).
    
    If random is True then instead generate exactly one schedule randomly chosen.
    """
    if vars is None:
        vars = halide.func_varlist(func)    
    #if func.name() == 'f':
    #    yield FragmentList(func, [random_module.choice(FragmentChunk.fragments(root_func, func, FragmentChunk, vars, extra_caller_vars))])
    #    return
    for depth in range(min_depth, max_depth+1):
        if random:
            depth = random_module.randrange(min_depth, max_depth+1)
        for L in schedules_depth(root_func, func, vars, depth, random, extra_caller_vars):
            if L.check():
                yield L.randomized_const()
                if random:
                    return

class Schedule:
    """
    Schedule applying (recursively) to all Funcs called by root_func.
    
    For example:
        f.vectorize(x).parallel(y)
        g.parallel(y)
    """
    def __init__(self, root_func, d):
        self.root_func = root_func
        self.d = d
    
    def __copy__(self):
        d = {}
        for name in self.d:
            d[name] = copy.copy(self.d[name])
        return Schedule(self.root_func, d)
    
    def __str__(self):
        d = self.d
        ans = []
        for key in sorted(d.keys()):
            s = str(d[key])
            #if s != '':
            ans.append(s)
        return '\n'.join(ans) #join(['-'*40] + ans + ['-'*40])

    def randomized_const(self):
        dnew = {}
        for name in self.d:
            dnew[name] = self.d[name].randomized_const()
        return Schedule(self.root_func, dnew)

    def new_vars(self):
        ans = []
        for x in self.d.values():
            ans.extend(x.new_vars())
        return list(sorted(set(ans)))

    def apply(self, verbose=False):   # Apply schedule
        #print 'apply schedule:'
        #print str(self)
        #halide.inline_all(self.root_func)
        scope = halide.all_vars(self.root_func)
        #print 'scope', scope.keys()
        new_vars = self.new_vars()
        #print 'new_vars', new_vars
        for varname in new_vars:
            scope[varname] = instantiate_var(varname)
        if verbose:
            print 'scope:', scope
        def callback(f, parent):
            name = f.name()
            if name in self.d:
                s = str(self.d[name])
                s = s.replace(name + '.', '__func.reset().')
                scope['__func'] = f
                #print 'apply', s
                exec s in scope
        halide.visit_funcs(self.root_func, callback)
    
    def test(self, shape, input, eval_func=None):
        """
        Test on zero array of the given shape. Return evaluate() function which when called returns the output Image.
        """
        print 'apply'
        self.apply()
        print 'in_image'
        in_image = numpy.zeros(shape, input.type().to_numpy())
        print 'filter'
        return halide.filter_image(input, self.root_func, in_image, eval_func=eval_func)
        
def random_schedule(root_func, min_depth=0, max_depth=DEFAULT_MAX_DEPTH, vars=None, constraints={}):
    """
    Generate Schedule for all functions called by root_func (recursively). Same arguments as schedules_func().
    """
    if vars is None:
        vars = halide.func_varlist(root_func)
    d_new_vars = {}
    schedule = {}
    def callback(f, parent):
        extra_caller_vars = d_new_vars.get(parent.name() if parent is not None else None,[])
#        print 'schedule', f.name(), extra_caller_vars
#        ans = schedules_func(root_func, f, min_depth, max_depth, random=True, extra_caller_vars=extra_caller_vars, vars=vars).next()
        name = f.name()
        if name in constraints:
            schedule[name] = constraints[name]
        else:
            max_depth_sel = max_depth # if f.name() != 'f' else 0
            ans = schedules_func(root_func, f, min_depth, max_depth_sel, random=True, extra_caller_vars=extra_caller_vars).next()
            schedule[name] = ans
        d_new_vars[name] = schedule[name].new_vars()
        
    halide.visit_funcs(root_func, callback)
    #print 'random_schedule', schedule
    return Schedule(root_func, schedule)

def func_lhs_var_names(f):
    ans = []
    for y in f.args():
        for x in y.vars():
            ans.append(x.name())
    return ans
    
def caller_vars(root_func, func):
    "Given a root Func and current function return list of variables of the caller."
    func_name = func.name()
    for (name, g) in halide.all_funcs(root_func).items():
        rhs_names = [x.name() for x in g.rhs().funcs()]
        if func_name in rhs_names:
            return func_lhs_var_names(g)
    return []

# --------------------------------------------------------------------------------------------------------------
# Autotuning via Genetic Algorithm (follows same ideas as PetaBricks)
# --------------------------------------------------------------------------------------------------------------

class AutotuneParams:
    pop_elitism_pct = 0.2
    pop_crossover_pct = 0.3
    pop_mutated_pct = 0.3
    tournament_size = 5 #3
    mutation_rate = 0.15
    population_size = 128 #64
    
    # Different mutation modes (mutation is applied to each Func independently)
    prob_mutate = {'replace': 0.2, 'consts': 0.2, 'add': 0.2, 'remove': 0.2, 'edit': 0.2}
    # 'replace' - Replace Func's schedule with a new random schedule
    # 'consts'  - Just modify constants when mutating
    # 'add'     - Add a single schedule macro to existing schedule
    # 'remove'  - Removing a single schedule macro from existing schedule
    # 'edit'    - Edit (replace) a single schedule macro within Func's schedule
    
    min_depth = 0
    max_depth = DEFAULT_MAX_DEPTH
    
    trials = 5                  # Timing runs per schedule
    generations = 500
    
    compile_timeout = 5.0
    run_timeout_mul = 3.0           # Fastest run time multiplied by this factor is cutoff
    run_timeout_default = 5.0       # Assumed 'fastest run time' before best run time is established
    
    num_print = 10

def sample_prob(d):
    "Randomly sample key from dictionary with probabilities given in values."
    items = d.items()
    Ptotal = sum(x[1] for x in items)
    cutoff = random.random()*Ptotal
    current = 0.0
    for (key, value) in items:
        current += value
        if current > cutoff:
            return key
    return key
    
def crossover(a, b):
    "Cross over two schedules, using 2 point crossover."
    funcL = halide.all_funcs(a.root_func, True)
    names = [x[0] for x in funcL]
    assert a.root_func is b.root_func
    aset = set(a.d.keys())
    assert aset == set(b.d.keys()) #== set(names)
    names = [x for x in names if x in aset]
    
    if random.randrange(2) == 0:
        (a, b) = (b, a)

    d = {}
    i1 = random.randrange(len(names))
    i2 = random.randrange(len(names))
    (i1, i2) = (min(i1, i2), max(i1, i2))
    if i1 == 0 and i2 == len(names)-1:
        i2 -= 1
    for i in range(len(names)):
        if i1 <= i <= i2:
            d[names[i]] = copy.copy(a.d[names[i]])
        else:
            d[names[i]] = copy.copy(b.d[names[i]])
    
    return Schedule(a.root_func, d)

def mutate(a, p):
    "Mutate existing schedule using AutotuneParams p."
    a0 = a
    a = copy.copy(a0)
    extra_caller_vars = []      # FIXME: Implement extra_caller_vars, important for chunk().
    
    while True:
        for name in a.d.keys():
            if random.random() < p.mutation_rate:
                dmutate = dict(p.prob_mutate)
                if len(a.d[name]) <= p.min_depth:
                    del dmutate['remove']
                if len(a.d[name]) >= p.max_depth:
                    del dmutate['add']
                if len(a.d[name]) == 0:
                    del dmutate['edit']
#                if 'remove' in dmutate:
#                    del dmutate['remove']
#                if 'edit' in dmutate:
#                    del dmutate['edit']
                
                mode = sample_prob(dmutate)
                try:
                    if mode == 'consts':
                        a.d[name] = a.d[name].randomized_const()
                    elif mode == 'replace':
                        constraints = a.d
                        del constraints[name]
                        all_d = random_schedule(a.root_func, p.min_depth, p.max_depth, None, constraints)
                        a.d[name] = all_d.d[name]
                    elif mode == 'add':
                        assert len(a.d[name]) < p.max_depth
                        #raise NotImplementedError
                        a.d[name] = a.d[name].added(a.root_func, extra_caller_vars)
                    elif mode == 'remove':
                        assert len(a.d[name]) > p.min_depth
                        #raise NotImplementedError
                        a.d[name] = a.d[name].removed()
                    elif mode == 'edit':
#                        raise NotImplementedError
                        a.d[name] = a.d[name].edited(a.root_func, extra_caller_vars)
                    else:
                        raise ValueError('Unknown mutation mode %s'%mode)
                except MutateFailed:
                    pass
        try:
            a.apply()       # Apply schedule to determine if random_schedule() invalidated new variables that were referenced
        except NameError:
            continue
        return a

def select_and_crossover(prevL, p, root_func):
    a = tournament_select(prevL, p, root_func)
    b = tournament_select(prevL, p, root_func)
    c = crossover(a, b)
    c = mutate(c, p)
    return c

def select_and_mutate(prevL, p, root_func):
    a = tournament_select(prevL, p, root_func)
    return mutate(a, p)

def tournament_select(prevL, p, root_func):
    i = random.randrange(p.tournament_size)
    if i >= len(prevL):
        return random_schedule(root_func, p.min_depth, p.max_depth)
    else:
        return copy.copy(prevL[i])

class Constraints:
    "Constraints([f, g]) excludes f, g from autotuning."
    def __init__(self, exclude=[]):
        self.exclude = exclude
    
    def constrain(self, schedule):
        "Return new Schedule instance with constraints applied."
#        if not 'tile' in str(schedule):
#            return random_schedule(schedule.root_func, 0, 0)
        d = {}
        exclude_names = set()
        for f in self.exclude:
            exclude_names.add(f.name())
        for name in schedule.d:
            if name not in exclude_names:
                d[name] = schedule.d[name]
        return Schedule(schedule.root_func, d)

def next_generation(prevL, p, root_func, constraints):
    """"
    Get next generation using elitism/mutate/crossover/random.
    
    Here prevL is list of Schedule instances sorted by decreasing fitness, and p is AutotuneParams.
    """
    ans = []
    schedule_strs = set()
    def append_unique(schedule):
        s = str(schedule)
        if s not in schedule_strs:
            schedule_strs.add(s)
            ans.append(schedule)
            
    def do_crossover():
        append_unique(constraints.constrain(select_and_crossover(prevL, p, root_func)))
    def do_mutated():
        append_unique(constraints.constrain(select_and_mutate(prevL, p, root_func)))
    def do_random():
        append_unique(constraints.constrain(random_schedule(root_func, p.min_depth, p.max_depth)))
        
    random_pct = 1-p.pop_mutated_pct-p.pop_crossover_pct-p.pop_elitism_pct

    for i in range(int(p.population_size*p.pop_elitism_pct)):
        if i < len(prevL):
            append_unique(prevL[i])
    for i in range(int(p.population_size*p.pop_crossover_pct)):
        do_crossover()
    for i in range(int(p.population_size*p.pop_mutated_pct)):
        do_mutated()
    for i in range(int(p.population_size*random_pct)):
        do_random()
        
    Pd = {'crossover': p.pop_crossover_pct, 'mutated': p.pop_mutated_pct, 'random': random_pct}

    while len(ans) < p.population_size:
        mode = sample_prob(Pd)
        if mode == 'crossover':
            do_crossover()
        elif mode == 'mutated':
            do_mutated()
        elif mode == 'random':
            do_random()
        else:
            raise ValueError('Unknown mode %s'%mode)

    return ans

class AutotuneTimer:
    compile_time = 0.0
    run_time = 0.0
    total_time = 0.0
    def __init__(self):
        self.start_time = time.time()
    
def time_generation(L, p, test_func, timer, display_text=''):
    #T0 = time.time()
    #Tcompile = [0.0]
    #Trun = [0.0]
    def time_schedule(current):
        info = test_func(current)()
        timer.compile_time += info['compile']
        timer.run_time += info['run']
        return info['time']
        #out = test_func(current)
        #current.apply()
        #T = []
        #for i in range(p.trials):
        #    dout = out()
        #    T.append(dout['time'])
        #return min(T) #sum(T)/len(T)
    
    ans = []
    success = 0
    for i in range(len(L)):
        Tstart = time.time()
        if AUTOTUNE_VERBOSE:
            print 'Timing %d/%d'%(i, len(L)),
        ans.append(time_schedule(L[i]))
        success += ans[-1] < COMPILE_TIMEOUT
        timer.total_time = time.time()-timer.start_time
        stats_str = 'pid=%d, %.0f%% succeed, compile time=%d secs, run time=%d secs, total=%d secs' % (os.getpid(), success*100.0/(i+1),timer.compile_time, timer.run_time, timer.total_time)
        if AUTOTUNE_VERBOSE:
            print '%.5f secs'%ans[-1]
        else:
            sys.stderr.write('\n'*100 + 'Testing %d/%d (%s)\n%s\n'%(i+1,len(L),stats_str,display_text))
            sys.stderr.flush()
            pass
    print 'Statistics: %s'%stats_str
    return ans

COMPILE_TIMEOUT = 10001.0
RUN_TIMEOUT     = 10002.0

def default_tester(input, out_func, p, in_image, allow_cache=True):
    cache = {}
    best_run_time = [p.run_timeout_default]
    
    def test_func(schedule):
        schedule_str = str(schedule)
        if schedule_str in cache and allow_cache:
            return lambda: cache[schedule_str]
        try:
            T0 = time.time()
            with Watchdog(p.compile_timeout):           # FIXME: Make these serial
                schedule.apply()
                evaluate = halide.filter_image(input, out_func, in_image)
                Tend_compile = time.time()
                Tcompile = Tend_compile-T0
                if AUTOTUNE_VERBOSE:
                    print 'compiled in', Tcompile, repr(str(schedule))
                def out():
                    T = []
                    for i in range(p.trials):
                        T0 = time.time()

                        try:
                            with Watchdog(best_run_time[0]*p.run_timeout_mul):
                                Iout = evaluate()
                        except Watchdog:
                            if AUTOTUNE_VERBOSE:
                                print 'run failed', repr(str(schedule))
                            ans = {'time': RUN_TIMEOUT, 'compile': Tcompile, 'run': time.time()-Tend_compile}
                            cache[schedule_str] = ans
                            return ans

                        T1 = time.time()
                        T.append(T1-T0)
                    T = min(T)
                    if T < best_run_time[0]:
                        best_run_time[0] = T
                    ans = {'time': T, 'compile': Tcompile, 'run': time.time()-Tend_compile}
                    cache[schedule_str] = ans
                    return ans
                return out
        except Watchdog:
            if AUTOTUNE_VERBOSE:
                print 'compile failed', repr(str(schedule))
            ans = {'time': COMPILE_TIMEOUT, 'compile': time.time()-T0, 'run': 0.0}
            cache[schedule_str] = ans
            return lambda: ans
    return test_func
"""

def default_tester(input, out_func, p, in_image, counter=[0]):
    evaluate = halide.filter_image(input, out_func, in_image)
    def test_func():
        out = evaluate()
        #out_np = numpy.asarray(out)
        #out.show()
        #out.save('out%d.png'%counter[0])
        counter[0] += 1
    return test_func
"""

#def autotune(input, out_func, p, tester=default_tester, tester_kw={'in_image': 'lena_crop2.png'}):
def autotune(input, out_func, p, tester=default_tester, tester_kw=DEFAULT_TESTER_KW, constraints=Constraints()):
    timer = AutotuneTimer()
    test_func = tester(input, out_func, p, **tester_kw)
    
    currentL = []
    display_text = ''
    for gen in range(p.generations):
        currentL = next_generation(currentL, p, out_func, constraints)
        timeL = time_generation(currentL, p, test_func, timer, display_text)
        
        bothL = sorted([(timeL[i], currentL[i]) for i in range(len(timeL))])
        display_text = '-'*40 + '\n'
        display_text += 'Generation %d'%(gen+1) + '\n'
        display_text += '-'*40 + '\n'
        for (j, (timev, current)) in list(enumerate(bothL))[:p.num_print]:
            display_text += '%.4f %s' % (timev, repr(str(current))) + '\n'
        display_text += '\n'
        print display_text
        sys.stdout.flush()
        
        currentL = [x[1] for x in bothL]

# --------------------------------------------------------------------------------------------------------------
# Unit Tests
# --------------------------------------------------------------------------------------------------------------

def test_crossover(verbose=False):
    (f, g) = test_funcs()
    constraints = Constraints()
    
    for j in range(8):
        random.seed(j)

#        for i in range(1000):
#            a = nontrivial_schedule(g)
#            a.apply()
#        print 'random_schedule:  OK'
        
        a = nontrivial_schedule(g)
        aL = str(a).split('\n')
        while True:
            b = nontrivial_schedule(g)
            bL = str(b).split('\n')
            all_different = True
            assert len(aL) == len(bL), (a, b, aL, bL)
            for i in range(len(aL)):
                if aL[i] == bL[i]:
                    all_different = False
                    break
            if all_different:
                break
        #print 'test_crossover'
        #print '----- Schedule A -----'
        #print a
        #print 
        #print '----- Schedule B -----'
        #print b
        #T0 = time.time()
        #print 'a'
        for i in range(80):
            c = crossover(a, b)
            c.apply()
            cL = str(c).split('\n')
            assert aL != bL and aL != cL and bL != cL
            if verbose:
                print '---- Crossover %d,%d ----'%(j,i)
                print c
        #T1 = time.time()
        
        p = AutotuneParams()
        #print 'b'
        for i in range(80):
            if verbose:
                print '---- Mutate after crossover %d,%d ----'%(j,i)
                print 'a', repr(str(a)), a.new_vars()
                print 'b', repr(str(b)), b.new_vars()
            c = crossover(a, b)
            if verbose:
                print 'c', repr(str(c)), c.new_vars()
            c.apply()
            c = mutate(c, p)
            if verbose:
                print 'cmutate', repr(str(c)), c.new_vars()
            c.apply()
    #        cL = str(c).split('\n')
    #        assert aL != bL and aL != cL and bL != cL

        #T2 = time.time()
        #print T1-T0, T2-T1
        
        def test_generation(L, prevL):
            assert len(L) == p.population_size
            for x in L:
                x.apply()
            current_set = set(str(x) for x in L)
            prev_set = set(str(x) for x in prevL)
            assert len(current_set) > 2
            if len(prev_set):
                assert len(current_set & prev_set) >= 1
        
        #random.seed(2)
        #print 'c'
        prev_gen = []
        for gen in range(2):
            L = next_generation(prev_gen, p, g, constraints)
            if j == 0 and verbose:
                for i in range(len(L)):
                    print 'gen=%d, i=%d'%(gen,i)
                    print L[i]
                    print '-'*20
            test_generation(L, prev_gen)
            prev_gen = L
    
    print 'crossover:         OK'
    print 'mutate:            OK'
    print 'next_generation:   OK'

def test_funcs(cache=[]):
    if len(cache):
        return cache[0]
    f = halide.Func('f')
    x = halide.Var('x')
    y = halide.Var('y')
    c = halide.Var('c')
    g = halide.Func('g')
    v = halide.Var('v')
    input = halide.UniformImage(halide.UInt(16), 3)
    int_t = halide.Int(32)
    f[x,y,c] = input[halide.clamp(x,halide.cast(int_t,0),halide.cast(int_t,input.width()-1)),
                     halide.clamp(y,halide.cast(int_t,0),halide.cast(int_t,input.height()-1)),
                     halide.clamp(c,halide.cast(int_t,0),halide.cast(int_t,2))]
    #g[v] = f[v,v]
    g[x,y,c] = f[x,y,c]+1
    cache.append((f, g))
    return (f, g)

def nontrivial_schedule(g):
    while True:
        r = random_schedule(g, 0, DEFAULT_MAX_DEPTH)
        s = str(r)
        if len(s) > 30:
            contains_num = False
            for i in range(65):
                if str(i) in s:
                    contains_num = True
                    break
            if contains_num:
                return r

def test_sample_prob():
    d = {'a': 0.3, 'b': 0.5, 'c': 0.2, 'd': 0.0}
    count = 10000
    dc = {'a': 0, 'b': 0, 'c': 0, 'd': 0}
    for i in range(count):
        key = sample_prob(d)
        dc[key] += 1
    eps = 0.05
    for key in d.keys():
        assert abs(dc[key]*1.0/count-d[key])<eps, (key, d[key], dc[key]*1.0/count)
    assert dc['d'] == 0
    print 'sample_prob:       OK'

def test_schedules(verbose=False, test_random=False):
    #random_module.seed(int(sys.argv[1]) if len(sys.argv)>1 else 0)
    halide.exit_on_signal()
    (f, g) = test_funcs()
    assert sorted(halide.all_vars(g).keys()) == sorted(['x', 'y', 'c']) #, 'v'])

    if verbose:
        print halide.func_varlist(f)
        print 'caller_vars(f) =', caller_vars(g, f)
        print 'caller_vars(g) =', caller_vars(g, g)
    
#    validL = list(valid_schedules(g, f, 4))
#    validL = [repr(_x) for _x in validL]
#    
#    for L in sorted(validL):
#        print repr(L)
    T0 = time.time()
    if not test_random:
        random = True #False
        nvalid_determ = 0
        for L in schedules_func(g, f, 0, 3):
            nvalid_determ += 1
            if verbose:
                print L
        nvalid_random = 0
        for i in range(100):
            for L in schedules_func(g, f, 0, DEFAULT_MAX_DEPTH, random=True): #sorted([repr(_x) for _x in valid_schedules(g, f, 3)]):
                if verbose and 0:
                    print L#repr(L)
                nvalid_random += 1
    s = []
    for i in range(1000):
        d = random_schedule(g, 1, DEFAULT_MAX_DEPTH)
        si = str(d)
        s.append(si)
        if verbose:
            print 'Schedule:'
            print '-'*20
            print si
            print '-'*20
            print
        sys.stdout.flush()
        d.apply()
        if test_random:
            evaluate = d.test((36, 36, 3), input)
            print 'evaluate'
            evaluate()
            if test_random:
                print 'Success'
                sys.exit()
    T1 = time.time()
    
    s = '\n'.join(s)
    assert 'f.chunk(_c0)' in s
    assert 'f.root().vectorize' in s
    assert 'f.root().unroll' in s
    assert 'f.root().split' in s
    assert 'f.root().tile' in s
    assert 'f.root().parallel' in s
    assert 'f.root().transpose' in s

    assert nvalid_random == 100
    if verbose:
        print 'generated in %.3f secs' % (T1-T0)

    print 'random_schedule:   OK'
    
    r = nontrivial_schedule(g)
    constantL = [str(r)]
#    print 'Randomizing the constants in a chosen schedule:'
    for i in range(100):
        #print r.randomized_const()
        #print
        constantL.append(str(r.randomized_const()))
    assert len(set(constantL)) > 1
    print 'randomized_const:  OK'
            
def test():
#    test_schedules(True)
    test_sample_prob()
    test_schedules()
    test_crossover()
    
def main():
    args = sys.argv[1:]
    if len(args) == 0:
        print 'autotune test|print|autotune examplename|test_sched'
        sys.exit(0)
    if args[0] == 'test':
        test()
    elif args[0] == 'test_random':
        global use_random_blocksize
        use_random_blocksize = False
        test_schedules(True, test_random=True)
    elif args[0] == 'print':
        nprint = 100
        if len(args) > 1:
            nprint = int(args[1])
        cache = set()
        nsuccess = 0
        nfail = 0
        for i in range(nprint):
            if os.path.exists('_out.txt'):
                os.remove('_out.txt')
            os.system('python autotune.py test_random > _out.txt 2> /dev/null')
            s = open('_out.txt', 'rt').read()
            success = 'Success' in s
            search = 'Schedule:\n' + '-'*20
            try:
                j = s.index(search)
            except:
                raise ValueError('Did not find schedule in output')
            schedule = s[j:s.index('-'*20,j+len(search))]
            #print 'Found schedule', schedule
            nsuccess += success
            nfail += not success
            if schedule not in cache:
                print 'Success' if success else 'Failed ', schedule
                sys.stdout.flush()
                cache.add(schedule)
        print
        print 'Number successful schedules: %d/%d (%d failed)' % (nsuccess, nprint, nfail)
    elif args[0] == 'autotune':
        if len(args) < 2:
            print >> sys.stderr, 'expected 2 arguments to autotune'
            sys.exit(1)
        examplename = args[1]
        (input, out_func, test_func, scope) = getattr(examples, examplename)()
        
        evaluate = halide.filter_image(input, out_func, DEFAULT_IMAGE)
        evaluate()
        T0 = time.time()
        evaluate()
        T1 = time.time()
        print 'Root all time: %.4f'%(T1-T0)
        
        p = AutotuneParams()
        constraints = Constraints()
        if 'input_clamped' in scope:
            constraints = Constraints([scope['input_clamped']])
        autotune(input, out_func, p, constraints=constraints)
    elif args[0] in ['test_sched']:
        #(input, out_func, test_func) = examples.blur()
        pass
    else:
        raise NotImplementedError('%s not implemented'%args[0])
#    test_schedules()
    
if __name__ == '__main__':
    main()
    