// Day 5 (P2): the studio's microphone as raw samples (docs/day5-design.md section 11). Each block
// of the first input channel is copied to the page, which writes the WAV the server expects.
class PcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) this.port.postMessage(channel.slice(0));
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
