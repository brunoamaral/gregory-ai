module.exports = function(RED) {
    "use strict";
    const he = require("he");

    function HeNode(config) {
        RED.nodes.createNode(this, config);
        this.property = config.property || "payload";
        this.propertyType = "msg";
        this.mode = config.mode;

        this.options = {
            encode: {
                strict: config.optionsStrict,
                useNamedReferences: config.optionsUseNamedReferences,
                decimal: config.optionsPreferDecimal,
                encodeEverything: config.optionsEncodeEverything,
                allowUnsafeSymbols: config.optionsAllowUnsafeSymbols
            },
            decode: {
                strict: config.optionsStrict,
                isAttributeValue: config.optionsIsAttributeValue
            }
        };

        let node = this;

        /**
         * Get a property value as selected from a typedInput widget; capable of dealing with msg/flow/global property types.
         * TODO: move these to a separate library for future usage.
         * @param {object} msg - the msg object to operate on
         * @param {string} prop - the property set by the typedInput
         * @param {string} propT - the property type as set by the typedInput
         * @returns {Promise<>}
         */
        function getValue(msg, prop, propT) {
            if (propT === 'msg') {
                return Promise.resolve(RED.util.getMessageProperty(msg, prop));
            }
            else if (propT === 'flow' || propT === 'global') {
                return new Promise(
                    (resolve, reject) => {
                        const ctxKey = RED.util.parseContextStore(prop);
                        node.context()[propT].get(ctxKey.key, ctxKey.store, (err, value) => {
                            if (err) {
                                reject(err);
                            }
                            else {
                                resolve(value);
                            }
                        });
                    }
                );
            }
        }

        /**
         * Set a value to the property selected in a typedInput widget; capable of dealing with msg/flow/global property types.
         * TODO: move these to a separate library for future usage.
         * @param msg - the msg object to operate on
         * @param {string} prop - the property set by the typedInput
         * @param {string} propT - the property type as set by the typedInput
         * @param value - the value to set.
         * @returns {Promise<>}
         */
        function setValue(msg, prop, propT, value) {
            if (propT === 'msg') {
                return Promise.resolve(RED.util.setMessageProperty(msg, prop, value, true));
            }
            else if (propT === 'flow' || propT === 'global') {
                return new Promise(
                    (resolve, reject) => {
                        const ctxKey = RED.util.parseContextStore(prop);
                        node.context()[propT].set(ctxKey.key, value, ctxKey.store, (err) => { // NOTE: does not match documentation but matches function signature
                            if (err) {
                                reject(err);
                            }
                            else {
                                resolve();
                            }
                        });
                    }
                );
            }
        }


        this.on('input', function(msg, send, done) {
            send = send || function() { node.send.apply(node,arguments) };

            getValue(msg, node.property, node.propertyType).then(function (value) {
                if (value !== undefined) {
                    switch (node.mode) {
                        case 'encode':
                            return he.encode(value, node.options.encode);
                        case 'escape':
                            return he.escape(value);
                        case 'decode':
                        case 'unescape':
                            return he.decode(value, node.options.decode);
                        default:
                            const err = `The selected mode ${node.mode} is not known.`;
                            throw new Error(err);
                    }
                }
                else {
                    throw new Error(`The selected property ${node.propertyType}.${node.property} does not exist.`);
                }
            }).then(function (value) {
                return setValue(msg, node.property, node.propertyType, value)
            }).then(function() {
                send(msg);
                done();
            }).catch(function (err) {
                if (done) {
                    // v1.0 and newer
                    done(err);
                }
                else {
                    // before v1.0
                    node.error(err, msg);
                }
            });
        });
    }

    RED.nodes.registerType("html-entities", HeNode);
};
