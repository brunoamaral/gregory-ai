module.exports = function(RED) {
    "use strict";
    var SqlString = require("sqlstring");

    function SqlStringFormat(config) {
        RED.nodes.createNode(this, config);

        var node = this;
        node.on("input", function(msg) {
            let varValues = config.vars
                .split(",")
                .map(v => v.trim())
                .filter(v => v !== "")
                .map(v => eval("msg." + v));
            msg = {
                ...msg,
                [config.outField]: SqlString.format(config.query, varValues),
            };
            node.send(msg);
        });
    }

    RED.nodes.registerType("sqlstring-format", SqlStringFormat);
};
