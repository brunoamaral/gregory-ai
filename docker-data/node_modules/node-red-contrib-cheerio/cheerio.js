module.exports = function (RED) {
    "use strict";
    var cheerio = require('cheerio');
    function CheerioNode(n) {
        RED.nodes.createNode(this, n);
        this.tag = n.tag;
        this.ret = n.ret || "html";
        this.as = n.as || "single";
        this.map = n.map || [];
        this.parseOptions = {
            xmlMode: n.xmlMode,
            decodeEntities: n.decodeEntities,
            lowerCaseTags: n.lowerCaseTags,
            lowerCaseAttributeNames: n.lowerCaseAttributeNames,
            recognizeCDATA: n.recognizeCDATA,
            recognizeSelfClosing: n.recognizeSelfClosing
        };
        var node = this;
        this.on("input", function (msg) {
            if (msg.hasOwnProperty("payload")) {
                var tag = node.tag;
                try {
                    var $ = cheerio.load(msg.payload, node.parseOptions);
                    var payloads = [];
                    $(tag).each(function () {
                        var payload = null;
                        switch (node.ret) {
                            case "html":
                                payload = $(this).html();
                                break;
                            case "text":
                                payload = $(this).text();
                                break;
                            case "object":
                                payload = {};
                                node.map.forEach(function(mapping) {
                                    var element = mapping.search ? this.find(mapping.search) : this;
                                    switch(mapping.ret) {
                                        case "html":
                                            payload[mapping.replace] = element.html();
                                            break;
                                        case "text":
                                            payload[mapping.replace] = element.text();
                                            break;
                                        case "attribute":
                                            payload[mapping.replace] = element.attr(mapping.attr);
                                    }
                                    
                                }, $(this));
                                break;
                        }
                        if (payload) {
                            switch (node.as) {
                                case "multi":
                                    msg.payload = payload;
                                    node.send(msg);
                                    break;
                                case "single":
                                    payloads.push(payload);
                                    break;
                            }
                        }
                    });
                    if ((node.as === "single") && (payloads.length !== 0)) {
                        msg.payload = payloads;
                        node.send(msg);
                    }
                } catch (error) {
                    node.error(error.message, msg);
                }
            } else {
                node.send(msg);
            }
        });
    }
    RED.nodes.registerType("cheerio", CheerioNode);
};