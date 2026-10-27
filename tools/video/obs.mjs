// A minimal obs-websocket v5 client (Node 22's WebSocket): identify with the password of
// %APPDATA%\obs-studio\user.ini, then send requests. The password is never printed.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

function settings() {
  const ini = readFileSync(`${process.env.APPDATA}\\obs-studio\\user.ini`, "utf8");
  const section = ini.split(/\[OBSWebSocket\]/)[1] ?? "";
  const get = (key) => new RegExp(`^${key}=(.*)$`, "m").exec(section)?.[1]?.trim();
  return { port: get("ServerPort") ?? "4455", password: get("ServerPassword") ?? "" };
}

const sha = (text) => createHash("sha256").update(text).digest("base64");

export async function connectObs() {
  const { port, password } = settings();
  const ws = new WebSocket(`ws://127.0.0.1:${port}`, "obswebsocket.json");
  const pending = new Map();
  let id = 0;
  let identified;
  const ready = new Promise((resolve, reject) => {
    identified = resolve;
    ws.onerror = () => reject(new Error("obs-websocket: cannot connect"));
  });
  ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.op === 0) {
      const auth = message.d.authentication;
      const data = { rpcVersion: 1, eventSubscriptions: 0 };
      if (auth) data.authentication = sha(sha(password + auth.salt) + auth.challenge);
      ws.send(JSON.stringify({ op: 1, d: data }));
    } else if (message.op === 2) {
      identified();
    } else if (message.op === 7) {
      const waiter = pending.get(message.d.requestId);
      if (!waiter) return;
      pending.delete(message.d.requestId);
      const status = message.d.requestStatus;
      if (status.result) waiter.resolve(message.d.responseData ?? {});
      else waiter.reject(new Error(`${waiter.type}: ${status.code} ${status.comment ?? ""}`));
    }
  };
  await ready;
  const call = (requestType, requestData = {}) =>
    new Promise((resolve, reject) => {
      const requestId = String(++id);
      pending.set(requestId, { resolve, reject, type: requestType });
      ws.send(JSON.stringify({ op: 6, d: { requestType, requestId, requestData } }));
    });
  return { call, close: () => ws.close() };
}
