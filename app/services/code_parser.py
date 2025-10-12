"""AST-based code parsing service for multi-language support"""
from typing import Dict, List, Optional, Any
import ast
import re
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class CodeParser:
    """Multi-language AST parsing with fallback to regex"""

    def __init__(self):
        # Initialize parsers for each language
        self.parsers = {
            "python": PythonParser(),
            "javascript": JavaScriptParser(),
            "typescript": TypeScriptParser(),
            "sql": SQLParser(),
            # Add more as needed
        }

    def parse(self, code: str, language: str) -> Dict[str, Any]:
        """
        Parse code and extract structural information

        Returns metadata dict with functions, classes, imports, etc.
        """
        if not settings.enable_ast_parsing:
            return self._basic_parse(code, language)

        # Get appropriate parser
        parser = self.parsers.get(language.lower())

        if parser:
            try:
                return parser.parse(code)
            except Exception as e:
                logger.warning(
                    "ast_parsing_failed",
                    language=language,
                    error=str(e)
                )
                # Fallback to basic parsing
                return self._basic_parse(code, language)
        else:
            # No parser for this language, use basic extraction
            return self._basic_parse(code, language)

    def _basic_parse(self, code: str, language: str) -> Dict[str, Any]:
        """Basic regex-based parsing when AST is not available"""
        return {
            "functions": [],
            "classes": [],
            "imports": [],
            "variables": [],
            "line_count": len(code.split('\n')),
            "char_count": len(code),
            "has_docstring": bool(re.search(r'""".*?"""|\'\'\'.*?\'\'\'', code, re.DOTALL)),
            "parser_type": "basic"
        }


class PythonParser:
    """Python-specific AST parsing using built-in `ast` module"""

    def parse(self, code: str) -> Dict[str, Any]:
        """Parse Python code using AST"""
        try:
            tree = ast.parse(code)

            metadata = {
                "functions": self._extract_functions(tree),
                "classes": self._extract_classes(tree),
                "imports": self._extract_imports(tree),
                "variables": self._extract_variables(tree),
                "decorators": self._extract_decorators(tree),
                "docstring": ast.get_docstring(tree),
                "has_docstring": bool(ast.get_docstring(tree)),
                "line_count": len(code.split('\n')),
                "complexity_score": self._calculate_complexity(tree),
                "parser_type": "ast"
            }

            return metadata

        except SyntaxError as e:
            logger.warning("python_syntax_error", error=str(e))
            raise

    def _extract_functions(self, tree: ast.AST) -> List[Dict]:
        """Extract function definitions"""
        functions = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Extract parameters
                params = [arg.arg for arg in node.args.args]

                # Extract return annotation
                return_type = None
                if node.returns:
                    return_type = ast.unparse(node.returns) if hasattr(ast, 'unparse') else str(node.returns)

                # Extract decorators
                decorators = []
                for decorator in node.decorator_list:
                    if hasattr(ast, 'unparse'):
                        decorators.append(ast.unparse(decorator))
                    else:
                        decorators.append(self._get_decorator_name(decorator))

                functions.append({
                    "name": node.name,
                    "parameters": params,
                    "return_type": return_type,
                    "decorators": decorators,
                    "docstring": ast.get_docstring(node),
                    "line_start": node.lineno,
                    "line_end": node.end_lineno if hasattr(node, 'end_lineno') else None,
                    "is_async": isinstance(node, ast.AsyncFunctionDef)
                })

        return functions

    def _extract_classes(self, tree: ast.AST) -> List[Dict]:
        """Extract class definitions"""
        classes = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # Extract base classes
                bases = []
                for base in node.bases:
                    if hasattr(ast, 'unparse'):
                        bases.append(ast.unparse(base))
                    elif isinstance(base, ast.Name):
                        bases.append(base.id)

                # Extract methods
                methods = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append(item.name)

                classes.append({
                    "name": node.name,
                    "bases": bases,
                    "methods": methods,
                    "docstring": ast.get_docstring(node),
                    "line_start": node.lineno,
                    "line_end": node.end_lineno if hasattr(node, 'end_lineno') else None
                })

        return classes

    def _extract_imports(self, tree: ast.AST) -> List[str]:
        """Extract import statements"""
        imports = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)

        return list(set(imports))  # Unique imports

    def _extract_variables(self, tree: ast.AST) -> List[str]:
        """Extract top-level variable assignments"""
        variables = []

        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        variables.append(target.id)

        return variables

    def _extract_decorators(self, tree: ast.AST) -> List[str]:
        """Extract all decorators used in code"""
        decorators = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for decorator in node.decorator_list:
                    if hasattr(ast, 'unparse'):
                        decorators.append(ast.unparse(decorator))
                    else:
                        decorators.append(self._get_decorator_name(decorator))

        return list(set(decorators))

    def _get_decorator_name(self, decorator) -> str:
        """Extract decorator name from AST node"""
        if isinstance(decorator, ast.Name):
            return decorator.id
        elif isinstance(decorator, ast.Call):
            if isinstance(decorator.func, ast.Name):
                return decorator.func.id
        return "unknown"

    def _calculate_complexity(self, tree: ast.AST) -> float:
        """Calculate cyclomatic complexity (simplified)"""
        complexity = 1  # Base complexity

        for node in ast.walk(tree):
            # Add 1 for each decision point
            if isinstance(node, (ast.If, ast.While, ast.For, ast.AsyncFor)):
                complexity += 1
            elif isinstance(node, ast.ExceptHandler):
                complexity += 1
            elif isinstance(node, (ast.And, ast.Or)):
                complexity += 1

        return complexity / 10.0  # Normalize to 0-10 scale


class JavaScriptParser:
    """JavaScript parser using regex patterns (AST parsing requires tree-sitter)"""

    def parse(self, code: str) -> Dict[str, Any]:
        """Parse JavaScript code using regex"""
        return {
            "functions": self._extract_functions(code),
            "classes": self._extract_classes(code),
            "imports": self._extract_imports(code),
            "variables": self._extract_variables(code),
            "line_count": len(code.split('\n')),
            "has_docstring": bool(re.search(r'/\*\*.*?\*/', code, re.DOTALL)),
            "parser_type": "regex"
        }

    def _extract_functions(self, code: str) -> List[Dict]:
        """Extract JavaScript functions"""
        functions = []

        # Pattern: function name(...) or const/let name = (...) => or async function
        patterns = [
            r'(?:async\s+)?function\s+(\w+)\s*\((.*?)\)',
            r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\((.*?)\)\s*=>',
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, code):
                name = match.group(1)
                params = [p.strip() for p in match.group(2).split(',') if p.strip()]

                functions.append({
                    "name": name,
                    "parameters": params,
                    "is_async": 'async' in match.group(0)
                })

        return functions

    def _extract_classes(self, code: str) -> List[Dict]:
        """Extract JavaScript classes"""
        classes = []

        # Pattern: class Name or class Name extends Base
        pattern = r'class\s+(\w+)(?:\s+extends\s+(\w+))?'

        for match in re.finditer(pattern, code):
            classes.append({
                "name": match.group(1),
                "extends": match.group(2) if match.group(2) else None
            })

        return classes

    def _extract_imports(self, code: str) -> List[str]:
        """Extract JavaScript imports"""
        imports = []

        # Pattern: import ... from 'module' or require('module')
        patterns = [
            r'import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]',
            r'require\([\'"]([^\'"]+)[\'"]\)'
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, code):
                imports.append(match.group(1))

        return list(set(imports))

    def _extract_variables(self, code: str) -> List[str]:
        """Extract JavaScript variable declarations"""
        variables = []

        # Pattern: const/let/var name = ...
        pattern = r'(?:const|let|var)\s+(\w+)\s*='

        for match in re.finditer(pattern, code):
            variables.append(match.group(1))

        return variables


class TypeScriptParser(JavaScriptParser):
    """TypeScript parser (extends JavaScript parser)"""

    def parse(self, code: str) -> Dict[str, Any]:
        """Parse TypeScript code"""
        metadata = super().parse(code)

        # Add TypeScript-specific extractions
        metadata["interfaces"] = self._extract_interfaces(code)
        metadata["types"] = self._extract_types(code)

        return metadata

    def _extract_interfaces(self, code: str) -> List[Dict]:
        """Extract TypeScript interfaces"""
        interfaces = []

        pattern = r'interface\s+(\w+)\s*\{'

        for match in re.finditer(pattern, code):
            interfaces.append({"name": match.group(1)})

        return interfaces

    def _extract_types(self, code: str) -> List[Dict]:
        """Extract TypeScript type aliases"""
        types = []

        pattern = r'type\s+(\w+)\s*='

        for match in re.finditer(pattern, code):
            types.append({"name": match.group(1)})

        return types


class SQLParser:
    """SQL parser using regex patterns"""

    def parse(self, code: str) -> Dict[str, Any]:
        """Parse SQL code"""
        return {
            "tables": self._extract_tables(code),
            "operations": self._extract_operations(code),
            "line_count": len(code.split('\n')),
            "parser_type": "regex"
        }

    def _extract_tables(self, code: str) -> List[str]:
        """Extract table names from SQL"""
        tables = []

        # Pattern: FROM table, JOIN table, INSERT INTO table, UPDATE table
        patterns = [
            r'FROM\s+(\w+)',
            r'JOIN\s+(\w+)',
            r'INSERT\s+INTO\s+(\w+)',
            r'UPDATE\s+(\w+)',
            r'CREATE\s+TABLE\s+(\w+)'
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, code, re.IGNORECASE):
                tables.append(match.group(1))

        return list(set(tables))

    def _extract_operations(self, code: str) -> List[str]:
        """Extract SQL operations"""
        operations = []

        sql_keywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'ALTER']

        for keyword in sql_keywords:
            if re.search(rf'\b{keyword}\b', code, re.IGNORECASE):
                operations.append(keyword)

        return operations


# Global instance
code_parser = CodeParser()
