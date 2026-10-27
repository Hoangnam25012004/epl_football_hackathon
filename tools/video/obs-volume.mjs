import { connectObs } from "./obs.mjs";

const obs = await connectObs();
await obs.call("SetInputVolume", { inputName: "Desktop sound", inputVolumeDb: Number(process.argv[2] ?? -5) });
console.log(await obs.call("GetInputVolume", { inputName: "Desktop sound" }));
obs.close();
