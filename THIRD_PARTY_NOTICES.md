# Third-party notices

The MIT license in [LICENSE](LICENSE) applies to this project's own code. Optional video and image workflows use third-party software under their respective terms.

- [Remotion 4.0.512](https://www.remotion.dev/) is used for optional Studio preview and MP4 rendering. Remotion uses a [special license](https://www.remotion.dev/docs/license); review it for the intended organization and use case before rendering.
- The optional Remotion renderer invokes its bundled FFmpeg/`ffprobe` binary to verify the generated media. The bundled build reports GPL version 2 or later; review the [FFmpeg legal and license information](https://ffmpeg.org/legal.html) when installing or redistributing optional runtime dependencies. This repository and its release assets do not bundle `node_modules` or that binary.
- `playwright-core` is distributed under the Apache License 2.0.
- React and React DOM are distributed under the MIT License.
- Chromium, Microsoft Edge and Google Chrome are not bundled or downloaded by this project. A locally installed browser remains subject to its vendor's license and policies.

This file is informational and does not replace the complete license text shipped by each dependency.
