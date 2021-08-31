let fs = require('fs')
let assert = require('assert');
let spawn = require('child_process').spawn
let net = require("net");

let PythonshellNode = require('../src/PythonShellNode');

describe('Pythonshell Node', function() {
	let venv = "/venv";

	before(function(done){
		this.timeout(10000);

		if (fs.existsSync(__dirname + venv)) {
	    done();
	    return;
	  }

	  console.log('creating virtual environment for testing')

		let spawn = require('child_process').spawn;
		let ve;
		try {
			ve = spawn('virtualenv', [__dirname + venv]);
		} catch (e){
			done(e);
		}

		ve.stdout.on('data', d=>console.log(d.toString()));
		ve.stderr.on('data', d=>console.log(d.toString()));

	  ve.on('close', function(code) {
	    if (code){
	      done(code);
	    } else{
	      try {
					let pipInstall = spawn(__dirname + venv + '/bin/pip', ['install', 'lxml']);
					pipInstall.stdout.on('data', d=>console.log(d.toString()));
					pipInstall.stderr.on('data', d=>console.log(d.toString()));
					pipInstall.on('close', done)
				} catch (e){
					done(e);
				}
	    }
	  });
	});

	describe('Failing cases', function(){
    it('should throw an error for empty config', function(done) {
    	try {
				let pyNode = new PythonshellNode();
				done(1)
    	} catch (e){
    		done()
    	}
    });

    it('should throw an error for empty config', function(done) {
    	try {
				let pyNode = new PythonshellNode({});
				done(1)
    	} catch (e){
    		done()
    	}
    });

    it('should throw an error for config without python file', function(done) {
    	try {
				let pyNode = new PythonshellNode({virtualenv: __dirname + venv});
				done(1)
    	} catch (e){
    		done()
    	}
    });

    it('should throw an error for non existing python file', function(done) {
    	try {
				let pyNode = new PythonshellNode({pyfile: __dirname + "/sample.p"});
				done(1)
    	} catch (e){
    		done()
    	}
    });

    it('should throw an error for non existing python virtualenv', function(done) {
    	try {
				let pyNode = new PythonshellNode({
					pyfile: __dirname + "/sample.py",
					virtualenv: __dirname + "/awefaewaf"
				});
				done(1)
    	} catch (e){
    		done()
    	}
    });

    it('should throw an error when importing external libraries without venv', function(done) {
			let pyNode = new PythonshellNode({pyfile: __dirname + "/sample-need-venv.py"});

			pyNode.onInput({payload: ""}, function(result){
			  done(1)
			}, function(err){
			  done()
			});
    });
	})


  describe('Run Python script', function() {
    it('should return the script result', function(done) {
			let pyNode = new PythonshellNode({
				pyfile: __dirname + "/sample.py"
			});

			pyNode.onInput({payload: ""}, function(result){
			  assert.notEqual(result.payload, null);
			  assert.equal(result.payload, 'hi');
			  done()
			}, function(err){
			  done(err)
			});
    });

    it('should output script ongoing result', function(done) {
    	this.timeout(10000);

    	let runs = 0;

			let pyNode = new PythonshellNode({
				pyfile: __dirname + "/sample-loop.py",
				continuous: true
			});

			pyNode.onInput({payload: ""}, function(result){
				assert.notEqual(result.payload, null);
				assert.equal(result.payload.trim(), 'on going')
				runs++;

				if (runs >= 3){
					pyNode.onClose()
			  	done();
				}
			}, function(err){
			  done(err)
			});
    });

    it('should not accepting input when is producing result', function(done) {
    	this.timeout(10000);

    	let ins = 0;
    	let runner;

			let pyNode = new PythonshellNode({
				pyfile: __dirname + "/sample-loop.py",
				continuous: true
			});

			pyNode.setStatusCallback(status=>{
				if (ins === 2 && status.text === "Not accepting input"){
					clearInterval(runner)
					pyNode.onClose()
			  	done()
				}
			})

			runner = setInterval(()=>{
				ins++
				pyNode.onInput({payload: "arg"},(result)=>{}, (err)=>{done(err)})
			}, 500)

			// TODO: to double check, look at ps aux | grep python 
    });

    it('should pass arguments to script', function(done) {
			let pyNode = new PythonshellNode({
				pyfile: __dirname + "/sample-with-arg.py"
			});

			pyNode.onInput({payload: "firstArg secondArg"}, function(result){
			  assert.notEqual(result.payload, null);
			  assert.equal(result.payload, 'firstArg secondArg');
			  done()
			}, function(err){
			  done(err)
			});
    });

    it('should support file read', function(done) {
			let pyNode = new PythonshellNode({
				pyfile: __dirname + "/sample-file-read.py"
			});

			pyNode.onInput({payload: ""}, function(result){
			  assert.notEqual(result.payload, null);
			  assert.equal(result.payload, fs.readFileSync(__dirname + '/test.txt', 'utf8'));
			  done()
			}, function(err){
			  done(err)
			});
    });

    it('should support virtual env', function(done) {
			let pyNode = new PythonshellNode({
				pyfile: __dirname + "/sample-need-venv.py",
				virtualenv: __dirname + venv
			});

			pyNode.onInput({payload: ""}, function(result){
			  assert.notEqual(result.payload, null);
			  assert.equal(result.payload, 'hi from venv');
			  done()
			}, function(err){
			  done(err)
			});
    });

    it('should support virtual env and file read', function(done) {
			let pyNode = new PythonshellNode({
				pyfile: __dirname + "/sample-need-venv-file-read.py",
				virtualenv: __dirname + venv
			});

			pyNode.onInput({payload: ""}, function(result){
			  assert.notEqual(result.payload, null);
			  assert.equal(result.payload, fs.readFileSync(__dirname + '/test.txt', 'utf8'));
			  done()
			}, function(err){
			  done(err)
			});
    });
  });

	describe('piping using unix socket', () => {

		it.skip('pipe', function(done) {
			let client
			let spawnCmd = 'python'//__dirname + '/' + venv + '/bin/' + 'python'
			let py1File = __dirname + "/sample-loop.py"
			let py2File = __dirname + "/stdin-data.py"

			let py1 = spawn(spawnCmd, ['-u', py1File])
			let py2 = spawn(spawnCmd, ['-u', py2File])

			py2.stdout.pipe(process.stdout)

			py1.stdout.on('data', d => {
				if (client)
					client.write(d)
			})

			let pipeServer = net.createServer(stream => {
	      stream.on('data', d => {
          py2.stdin.write(d)
	      })
	    })
	    pipeServer.listen('./abc')

			client = net.connect('./abc', console.log)
		})

		it.skip('work stdin-data', function(done) {
			this.timeout(10000);

		  let spawnCmd = __dirname + '/' + venv + '/bin/' + 'python'
			let stdinDataFile = __dirname + "/stdin-data.py"

			let child = spawn(spawnCmd, ['-u', stdinDataFile])

			setInterval(()=>{
				child.stdin.write("abc\n")
			},1000)

			child.stdout.pipe(process.stdout);
    });

		it('send data to python script stdin', function(done) {
			// TODO: here test just one input

			let pyNode = new PythonshellNode({
				pyfile: __dirname + "/stdin-data.py",
				stdInData: true
			});

			pyNode.onInput({payload: "abc\n"}, function(result){
			  assert.equal(result.payload.trim(), "abc");
			  done()
			}, function(err){
			  done(err)
			});

			setTimeout(()=>{
				pyNode.onClose()
			}, 1000)
    });
	})
});