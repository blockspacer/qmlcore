import json
import re
from compiler.js import split_name, escape_package, get_package, escape_id
from compiler.js.component import component_generator
from collections import OrderedDict
import os.path

import jinja2 as j2

root_type = 'core.CoreObject'
TEMPLATE_DIR = os.path.dirname(__file__)

class generator(object):
	def __init__(self, ns, bid):
		self.module = False
		self.ns, self.bid = ns, bid
		self.components = {}
		self.used_packages = set()
		self.used_components = set()
		self.imports = OrderedDict()
		self.packages = {}
		self.startup = []
		self.l10n = {}
		self.id_set = set(['context', 'model'])
		with open(os.path.join(TEMPLATE_DIR, 'template.js')) as f:
			self.template = j2.Template(f.read())
		with open(os.path.join(TEMPLATE_DIR, 'copy_args.js')) as f:
			self.copy_args = j2.Template(f.read())

	def add_component(self, name, component, declaration):
		if name in self.components:
			raise Exception("duplicate component " + name)

		package, component_name = split_name(name)
		package = escape_package(package)

		if not declaration:
			name = "%s.Ui%s" %(package, component_name[0].upper() + component_name[1:])
			self.used_components.add(name)
			self.used_packages.add(package)
			self.startup.append("\tcontext.start(new qml.%s(context))" %name)
			self.startup.append("\tcontext.run()")
		else:
			name = package + '.' + component_name

		if package not in self.packages:
			self.packages[package] = set()
		self.packages[package].add(component_name)

		gen = component_generator(self.ns, name, component, True)
		self.components[name] = gen

	def add_js(self, name, data):
		if name in self.imports:
			raise Exception("duplicate js name " + name)
		self.imports[name] = data

	def wrap(self, code, use_globals = False):
		return "(function() {/** @const */\nvar exports = %s;\n%s\nreturn exports;\n} )" %("_globals" if use_globals else "{}", code)

	def find_component(self, package, name, register_used = True):
		if name == "CoreObject":
			return root_type

		original_name = name
		name_package, name = split_name(name)

		candidates = []
		for package_name, components in self.packages.iteritems():
			if name in components:
				if name_package:
					#match package/subpackage
					if package_name != name_package and not package_name.endswith('.' + name_package):
						continue
				candidates.append(package_name)

		if not candidates:
			raise Exception("component %s was not found" %(original_name))

		if len(candidates) > 1:
			if name_package in candidates: #specified in name, e.g. core.Text
				package_name = name_package
			if package in candidates: #local to current package
				package_name = package
			elif 'core' in candidates: #implicit core lookup
				package_name = 'core'
			else:
				raise Exception("ambiguous component %s, you have to specify one of the packages explicitly: %s" \
					%(name, " ".join(map(lambda p: "%s.%s" %(p, name), candidates))))
		else:
			package_name = candidates[0]

		if register_used:
			self.used_components.add(package_name + '.' + name)
		return "%s.%s" %(package_name, name)

	def generate_component(self, gen):
		self.used_packages.add(gen.package)

		context = {'type': gen.name}
		context['code'] = gen.generate(self).decode('utf-8')
		context['prototype'] = gen.generate_prototype(self).decode('utf-8')
		return context

	used_re = re.compile(r'@using\s*{(.*?)}')

	def scan_using(self, code):
		for m in generator.used_re.finditer(code):
			name = m.group(1).strip()
			package, component_name = split_name(name)
			package = escape_package(package)
			self.used_components.add(name)
			self.used_packages.add(package)

	def generate_components(self):
		context_type = self.find_component('core', 'Context')
		context_gen = self.components[context_type]
		for i, pi in enumerate(context_gen.properties):
			for j, nv in enumerate(pi.properties):
				if nv[0] == 'buildIdentifier':
					bid = '"' + self.bid + '"'
					pi.properties[j] = (nv[0], bid.encode('utf-8'))
					break

		queue = ['core.Context']
		code, base_class = {}, {}
		code[root_type] = ''

		for gen in self.components.itervalues():
			gen.pregenerate(self)

		while queue or self.used_components:
			for component in self.used_components:
				if component not in code:
					queue.append(component)
			self.used_components = set()

			if queue:
				name = queue.pop(0)
				component = self.components[name]
				base_type = self.find_component(component.package, component.component.name)
				base_class[name] = base_type

				if name not in code:
					code[name] = self.generate_component(component)

		r = []
		order = []
		visited = set([root_type])
		def visit(type):
			if type in visited:
				return
			visit(base_class[type])
			order.append(type)
			visited.add(type)

		for type in base_class.iterkeys():
			visit(type)

		for type in order:
			r.append(code[type])

		return r

	def generate_prologue(self):
		for name in self.imports.iterkeys():
			self.used_packages.add(get_package(name))

		r = []
		packages = {}
		for package in sorted(self.used_packages):
			path = package.split(".")
			ns = packages
			for p in path:
				if p not in ns:
					ns[p] = {}
				ns = ns[p]

		path = "_globals"
		def check(path, packages):
			for ns in packages.iterkeys():
				if not ns:
					raise Exception('internal bug, empty name in packages')
				package = escape_package(path + "." + ns)
				r.append("if (!%s) /** @const */ %s = {}" %(package, package))
				check(package, packages[ns])
		check(path, packages)

		if 'core.core' in self.imports:
			r.append(self.generate_import('core.core', self.imports['core.core']))
		return '\n'.join(r)

	def generate_import(self, name, code):
		r = []
		safe_name = name
		if safe_name.endswith(".js"):
			safe_name = safe_name[:-3]
		safe_name = escape_package(safe_name.replace('/', '.'))
		code = "//=====[import %s]=====================\n\n" %name + code.decode('utf-8')
		r.append("_globals.%s = %s()" %(safe_name, self.wrap(code, name == "core.core"))) #hack: core.core use _globals as its exports
		return "\n".join(r)


	def generate_imports(self):
		r = ''
		for name, code in self.imports.iteritems():
			if name != 'core.core':
				r += self.generate_import(name, code) + '\n'
		return r

	re_copy_args = re.compile(r'COPY_ARGS\w*\((.*?),(.*?)(?:,(.*?))?\)')

	def generate(self, app, strict = True, release = False, verbose = False, manifest = {}, project_dirs = []):
		init_js = ''
		for project_dir in project_dirs:
			init_path = os.path.join(project_dir, '.core.js')
			if os.path.exists(init_path):
				if verbose:
					print 'including platform initialisation file at %s' %init_path
				with open(init_path) as f:
					init_js += f.read()

		init_js = self.replace_args(init_js)

		def write_properties(prefix, props):
			r = ''
			for k, v in sorted(props.iteritems()):
				k = escape_id(k)
				if isinstance(v, dict):
					r += write_properties(prefix + '$' + k, v)
				else:
					r += "var %s$%s = %s\n" %(prefix, k, json.dumps(v))
			return r

		manifest_prologue = write_properties('$manifest', manifest)

		#finding explicit @using declarations in code
		for name, code in self.imports.iteritems():
			self.scan_using(code)

		components = self.generate_components() #must be called first, generates used_packages/components sets
		prologue = self.generate_prologue()
		imports = self.generate_imports()

		text = self.template.render({
			'components': components,
			'prologue': prologue,
			'imports': imports,
			'strict': strict,
			'release': release,
			'manifest': manifest_prologue,
			'startup': "\n".join(self.startup),
			'ns': self.ns,
			'app': app,
			'l10n': json.dumps(self.l10n),
			'context_type': self.find_component('core', 'Context')
		})

		text = text.replace('/* ${init.js} */', init_js)
		return self.replace_args(text)

	def replace_args(self, text):
		#COPY_ARGS optimization
		def copy_args(m):
			name, idx, prefix = m.group(1).strip(), int(m.group(2).strip()), m.group(3)
			context = { 'name': name, 'index': idx, 'prefix': prefix, 'extra': 1 - idx, 'source': m.group(0) }
			return self.copy_args.render(context)

		text = generator.re_copy_args.sub(copy_args, text)
		return text

	def add_ts(self, path):
		from compiler.ts import Ts
		ts = Ts(path)
		lang = ts.language
		if lang is None: #skip translation without target language (autogenerated base)
			print 'WARNING: no language in %s, translation ignored' %path
			return
		data = {}
		for ctx in ts:
			for msg in ctx:
				source, type, text = msg.source, msg.translation.type, msg.translation.text
				if type == 'just-obsoleted':
					texts = data.setdefault(source, {})
					texts[ctx.name] = text
		if data:
			self.l10n[lang] = data
