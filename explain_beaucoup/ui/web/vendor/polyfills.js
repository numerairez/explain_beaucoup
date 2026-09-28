/* Polyfills for Qt5 WebEngine (Chromium 83-87) running a modern Vega build.
   Loaded before vega.min.js. Every shim is a no-op on engines that already
   provide the feature, so this file is harmless under Qt6/PyQt6 too. */
(function () {
  "use strict";

  // Chromium 93
  if (typeof Object.hasOwn !== "function") {
    Object.defineProperty(Object, "hasOwn", {
      value: function (obj, key) {
        if (obj == null) throw new TypeError("Cannot convert undefined or null to object");
        return Object.prototype.hasOwnProperty.call(Object(obj), key);
      },
      writable: true, configurable: true,
    });
  }

  // Chromium 98. Structural clone over the data shapes Vega specs contain.
  if (typeof globalThis.structuredClone !== "function") {
    globalThis.structuredClone = function structuredClone(input) {
      var seen = new Map();
      function clone(v) {
        if (v === null || typeof v !== "object") return v;
        if (seen.has(v)) return seen.get(v);
        var out;
        if (v instanceof Date) return new Date(v.getTime());
        if (v instanceof RegExp) return new RegExp(v.source, v.flags);
        if (v instanceof Map) {
          out = new Map(); seen.set(v, out);
          v.forEach(function (val, key) { out.set(clone(key), clone(val)); });
          return out;
        }
        if (v instanceof Set) {
          out = new Set(); seen.set(v, out);
          v.forEach(function (val) { out.add(clone(val)); });
          return out;
        }
        if (ArrayBuffer.isView(v)) return new v.constructor(v);
        if (v instanceof ArrayBuffer) return v.slice(0);
        if (Array.isArray(v)) {
          out = new Array(v.length); seen.set(v, out);
          for (var i = 0; i < v.length; i++) out[i] = clone(v[i]);
          return out;
        }
        // Plain object: Vega relies on the result's prototype being Object.prototype.
        out = {}; seen.set(v, out);
        for (var k in v) {
          if (Object.prototype.hasOwnProperty.call(v, k)) out[k] = clone(v[k]);
        }
        return out;
      }
      return clone(input);
    };
  }

  // Chromium 92
  function at(n) {
    var len = this.length;
    n = Math.trunc(Number(n) || 0);
    if (n < 0) n += len;
    return (n < 0 || n >= len) ? undefined : this[n];
  }
  if (!Array.prototype.at) {
    Object.defineProperty(Array.prototype, "at", { value: at, writable: true, configurable: true });
  }
  if (!String.prototype.at) {
    Object.defineProperty(String.prototype, "at", { value: at, writable: true, configurable: true });
  }

  // Chromium 85
  if (!String.prototype.replaceAll) {
    Object.defineProperty(String.prototype, "replaceAll", {
      value: function (search, replacement) {
        if (search instanceof RegExp) {
          if (!search.global) throw new TypeError("replaceAll must be called with a global RegExp");
          return this.replace(search, replacement);
        }
        return this.split(String(search)).join(
          typeof replacement === "function" ? undefined : String(replacement));
      },
      writable: true, configurable: true,
    });
  }

  // Chromium 97
  if (!Array.prototype.findLast) {
    Object.defineProperty(Array.prototype, "findLast", {
      value: function (fn, thisArg) {
        for (var i = this.length - 1; i >= 0; i--) {
          if (fn.call(thisArg, this[i], i, this)) return this[i];
        }
        return undefined;
      }, writable: true, configurable: true,
    });
  }
  if (!Array.prototype.findLastIndex) {
    Object.defineProperty(Array.prototype, "findLastIndex", {
      value: function (fn, thisArg) {
        for (var i = this.length - 1; i >= 0; i--) {
          if (fn.call(thisArg, this[i], i, this)) return i;
        }
        return -1;
      }, writable: true, configurable: true,
    });
  }

  // Chromium 85
  if (typeof Promise.any !== "function") {
    Promise.any = function (iterable) {
      var items = Array.from(iterable);
      return new Promise(function (resolve, reject) {
        var errors = [], pending = items.length;
        if (!pending) return reject(new Error("All promises were rejected"));
        items.forEach(function (p, i) {
          Promise.resolve(p).then(resolve, function (e) {
            errors[i] = e;
            if (--pending === 0) reject(new Error("All promises were rejected"));
          });
        });
      });
    };
  }
})();
