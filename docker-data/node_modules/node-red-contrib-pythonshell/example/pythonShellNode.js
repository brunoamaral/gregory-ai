var PythonshellNode = require('../src/PythonShellNode');

var pyNode = new PythonshellNode({
	pyfile: "./test/sample.py",
	virtualEnv: "./test/venv",
});

pyNode.onInput({payload: ""}, console.log, console.log);