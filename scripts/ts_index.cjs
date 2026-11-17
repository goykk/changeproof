/**
 * TypeScript/TSX 索引器（P2 多语言，索引级支持）。
 *
 * 用 apps/web 已安装的 TypeScript 编译器解析文件，输出符号与导入边的 JSON：
 *   node scripts/ts_index.cjs <root>
 * 输出：{ root, symbols: [{id, kind, name, file, line}], edges: [{source, target, type, line}] }
 * 限制：只做索引级支持（函数/类/箭头函数/导入/测试文件），不执行 TS 测试。
 */
const fs = require("fs");
const path = require("path");

const tsPath = path.join(__dirname, "..", "apps", "web", "node_modules", "typescript");
const ts = require(tsPath);

const root = path.resolve(process.argv[2] || ".");
const files = [];

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (["node_modules", "dist", ".next", ".git"].includes(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (/\.(ts|tsx)$/.test(entry.name) && !entry.name.endsWith(".d.ts")) files.push(full);
  }
}
walk(root);

const symbols = [];
const edges = [];
const rel = (p) => path.relative(root, p).replace(/\\/g, "/");

for (const file of files) {
  const relPath = rel(file);
  const text = fs.readFileSync(file, "utf8");
  const scriptKind = file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, scriptKind);
  const lineOf = (node) => source.getLineAndCharacterOfPosition(node.getStart()).line + 1;

  symbols.push({ id: relPath, kind: "file", name: relPath, file: relPath, line: 1 });

  for (const stmt of source.statements) {
    if (ts.isFunctionDeclaration(stmt) && stmt.name) {
      const name = stmt.name.text;
      symbols.push({ id: `${relPath}::${name}`, kind: "function", name, file: relPath, line: lineOf(stmt) });
      edges.push({ source: relPath, target: `${relPath}::${name}`, type: "defines", line: lineOf(stmt) });
    } else if (ts.isClassDeclaration(stmt) && stmt.name) {
      const name = stmt.name.text;
      symbols.push({ id: `${relPath}::${name}`, kind: "class", name, file: relPath, line: lineOf(stmt) });
      edges.push({ source: relPath, target: `${relPath}::${name}`, type: "defines", line: lineOf(stmt) });
    } else if (ts.isVariableStatement(stmt)) {
      for (const decl of stmt.declarationList.declarations) {
        if (
          ts.isIdentifier(decl.name) &&
          decl.initializer &&
          (ts.isArrowFunction(decl.initializer) || ts.isFunctionExpression(decl.initializer))
        ) {
          const name = decl.name.text;
          symbols.push({ id: `${relPath}::${name}`, kind: "function", name, file: relPath, line: lineOf(decl) });
        }
      }
    } else if (
      ts.isImportDeclaration(stmt) &&
      stmt.moduleSpecifier &&
      ts.isStringLiteral(stmt.moduleSpecifier) &&
      stmt.moduleSpecifier.text.startsWith(".")
    ) {
      const base = path.resolve(path.dirname(file), stmt.moduleSpecifier.text);
      const candidates = [
        `${base}.ts`,
        `${base}.tsx`,
        path.join(base, "index.ts"),
        path.join(base, "index.tsx"),
      ];
      const target = candidates.find((candidate) => fs.existsSync(candidate));
      if (target) edges.push({ source: relPath, target: rel(target), type: "imports", line: lineOf(stmt) });
    }
  }

  if (/\.(test|spec)\.(ts|tsx)$/.test(relPath)) {
    symbols.push({ id: relPath, kind: "test", name: relPath, file: relPath, line: 1 });
  }
}

process.stdout.write(JSON.stringify({ root: rel(root), symbols, edges }));