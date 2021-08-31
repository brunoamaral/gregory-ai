Executing a python script from node-red. Input to the node will become the argument for the python script, output of the script will be sent to output of the node.

Now supporting executing within a virtual environment. Specify the path to the virtualenv folder in node configuration.

Example flow:

```
[{"id":"a1b2b31b.65fe7","type":"tab","label":"Flow 1"},{"id":"3df34b3a.b6bb8c","type":"pythonshell in","z":"a1b2b31b.65fe7","name":"","pyfile":"/Users/namtrang/main.py","x":341.5,"y":154,"wires":[["f811cd5c.e9dfe8"]]},{"id":"f4dcbeae.1da998","type":"inject","z":"a1b2b31b.65fe7","name":"","topic":"","payload":"","payloadType":"date","repeat":"","crontab":"","once":false,"x":140.5,"y":76,"wires":[["3df34b3a.b6bb8c"]]},{"id":"f811cd5c.e9dfe8","type":"debug","z":"a1b2b31b.65fe7","name":"","active":true,"console":"false","complete":"false","x":537.5,"y":233,"wires":[]}]
```

And this is the content of the python script: 

```
import sys
print "Got arguments: ", sys.argv
```