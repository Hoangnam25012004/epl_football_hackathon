// Scene "Video": the primary monitor (no cursor) + the desktop's sound, recorded to C:\pundit-video\raw.
import { mkdirSync } from "node:fs";
import { connectObs } from "./obs.mjs";

const obs = await connectObs();
const version = await obs.call("GetVersion");
console.log("OBS", version.obsVersion, "websocket", version.obsWebSocketVersion);
const { scenes } = await obs.call("GetSceneList");
if (!scenes.some((s) => s.sceneName === "Video")) await obs.call("CreateScene", { sceneName: "Video" });
const { inputs } = await obs.call("GetInputList");
const names = inputs.map((i) => `${i.inputName} (${i.inputKind})`);
console.log("inputs:", names.join(", "));
if (!inputs.some((i) => i.inputName === "Screen")) {
  await obs.call("CreateInput", {
    sceneName: "Video",
    inputName: "Screen",
    inputKind: "monitor_capture",
    inputSettings: { capture_cursor: false },
  });
}
const { propertyItems } = await obs.call("GetInputPropertiesListPropertyItems", {
  inputName: "Screen",
  propertyName: "monitor_id",
});
console.log("monitors:", propertyItems.map((p) => p.itemName).join(" | "));
const primary =
  propertyItems.find((p) => /1920x1080/.test(p.itemName) && /Primary|Chính/i.test(p.itemName)) ??
  propertyItems.find((p) => /1920x1080/.test(p.itemName));
if (!primary) throw new Error("no 1920x1080 monitor");
await obs.call("SetInputSettings", {
  inputName: "Screen",
  inputSettings: { monitor_id: primary.itemValue, capture_cursor: false, method: 0 },
});
console.log("capturing:", primary.itemName);
const special = await obs.call("GetSpecialInputs");
console.log("desktop audio:", special.desktop1 ?? "none");
if (!special.desktop1 && !inputs.some((i) => i.inputName === "Desktop sound")) {
  await obs.call("CreateInput", {
    sceneName: "Video",
    inputName: "Desktop sound",
    inputKind: "wasapi_output_capture",
    inputSettings: { device_id: "default" },
  });
}
if (special.mic1) await obs.call("SetInputMute", { inputName: special.mic1, inputMuted: true });
await obs.call("SetCurrentProgramScene", { sceneName: "Video" });
mkdirSync("C:\\pundit-video\\raw", { recursive: true });
await obs.call("SetRecordDirectory", { recordDirectory: "C:\\pundit-video\\raw" });
const shot = await obs.call("GetSourceScreenshot", {
  sourceName: "Video", imageFormat: "png", imageWidth: 640, imageHeight: 360,
});
const { writeFileSync } = await import("node:fs");
writeFileSync("C:\\pundit-video\\raw\\probe.png", Buffer.from(shot.imageData.split(",")[1], "base64"));
console.log("probe screenshot: C:\\pundit-video\\raw\\probe.png");
obs.close();
