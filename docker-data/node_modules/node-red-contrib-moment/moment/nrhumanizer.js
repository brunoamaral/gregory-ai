/**
* Copyright (c) 2018 Julian Knight (Totally Information)
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
**/

// JK Note: This code contributed by (Contributed by Laro88, https://github.com/Laro88)
//          Tidying done by JK. Also removed dependency on validator which is vastly OTT for just ensuring an integer
//          2017-04-13

// Node for Node-Red that humalizes a timespan msg.payload.[] //TODO whould this be extended to work on Date objects as well and if so how?
// It is helpful when working with ui things that informs users of time since last operation etc.

// require moment.js (must be installed from package.js as a dependency)
var moment = require('moment-timezone');

// Module name must match this nodes html file
var moduleName = 'humanizer';

module.exports = function(RED) {
  'use strict';

  // The main node definition - most things happen in here
  function nodeGo(config) {
    // Create a RED node
    RED.nodes.createNode(this,config);

    // Store local copies of the node configuration (as defined in the .html)
    this.topic = config.topic;
    this.input = config.input || 'payload'; // where to take the input from

    // copy "this" object in case we need it in context of callbacks of other functions.
    var node = this;

    // respond to inputs....
    node.on('input', function (msg) {
      'use strict'; // We will be using eval() so lets get a bit of safety using strict

      //console.log(this.input);
      var v = msg.payload.hasOwnProperty(this.input) ? msg.payload[this.input] : msg.payload;
      // JK: Replace validator with native JS INT check, improve warning message
      //v = v;
      //if( !validator.isInt(v) ) {
      if( ! Number.isInteger(v) ) {
        return node.warn('Invalid input for humanize call, input must be an integer');
      }

      // JK: Pass seconds to duration - remove *1000 ms conversion
      var _humanized = moment.duration(v, 'seconds').humanize();
      if(typeof(msg.payload) == 'object'){
        msg.payload.humanized = _humanized;
      }
      else{
        msg.payload = {'humanized':_humanized};
      }

      node.send(msg);
    });

  } // ---- end of nodeGo function ---- //

  // Register the node by name. This must be called before overriding any of the
  // Node functions.
  RED.nodes.registerType(moduleName,nodeGo);
};
