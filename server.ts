import express, { Request, Response } from "express";
import cors from "cors";
import path from "path";
import fs from "fs";
import os from "os";
import { fileURLToPath } from "url";
import { youtubeDl as ydlExec } from "youtube-dl-exec";
import ytdl from "@distube/ytdl-core";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = 3000;
const HOST = "0.0.0.0";

app.use(cors());
app.use(express.json());

const ALLOWED_HOSTS = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtu.be",
]);

function isValidYouTubeUrl(urlStr: string): boolean {
  try {
    const u = new URL(urlStr);
    return (
      (u.protocol === "http:" || u.protocol === "https:") &&
      (ALLOWED_HOSTS.has(u.hostname) || ALLOWED_HOSTS.has(u.hostname.replace(/^www\./, "")))
    );
  } catch {
    return false;
  }
}

function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const mins = Math.floor(s / 60);
  const remSecs = s % 60;
  return `${mins}:${remSecs.toString().padStart(2, "0")}`;
}

function sanitizeFilename(filename: string): string {
  return filename.replace(/[/\\?%*:|"<>]/g, "_").trim() || "download";
}

// Serve root index.html
app.get("/", (_req: Request, res: Response) => {
  res.sendFile(path.join(__dirname, "index.html"));
});

// Options preflight
app.options("/api/info", (_req: Request, res: Response) => {
  res.sendStatus(204);
});

// POST /api/info
app.post("/api/info", async (req: Request, res: Response) => {
  const url = (req.body?.url || "").toString().trim();
  if (!isValidYouTubeUrl(url)) {
    return res.status(400).json({ error: "Please enter a valid YouTube URL." });
  }

  // 1. Try youtube-dl-exec first
  try {
    const info: any = await (ydlExec as any)(url, {
      dumpSingleJson: true,
      noWarnings: true,
      noPlaylist: true,
    });

    const formats = info.formats || [];
    const heightSet = new Set<number>();
    for (const f of formats) {
      if (typeof f.height === "number" && f.height >= 144) {
        heightSet.add(f.height);
      }
    }
    const heights = Array.from(heightSet).sort((a, b) => b - a);
    const durationSec = Number(info.duration) || 0;

    return res.json({
      title: info.title || "YouTube Video",
      channel: info.uploader || info.channel || "",
      thumbnail: info.thumbnail || "",
      duration: formatDuration(durationSec),
      heights,
    });
  } catch (err: any) {
    console.warn("youtube-dl-exec info failed, trying ytdl-core fallback:", err?.message);
  }

  // 2. Try @distube/ytdl-core fallback
  try {
    const info = await ytdl.getInfo(url);
    const formats = info.formats || [];
    const heightSet = new Set<number>();
    for (const f of formats) {
      if (f.height && f.height >= 144) {
        heightSet.add(f.height);
      }
    }
    const heights = Array.from(heightSet).sort((a, b) => b - a);
    const durationSec = Number(info.videoDetails.lengthSeconds) || 0;

    return res.json({
      title: info.videoDetails.title || "YouTube Video",
      channel: info.videoDetails.author?.name || "",
      thumbnail: info.videoDetails.thumbnails?.[info.videoDetails.thumbnails.length - 1]?.url || "",
      duration: formatDuration(durationSec),
      heights,
    });
  } catch (err: any) {
    console.warn("ytdl-core info failed, trying oEmbed fallback:", err?.message);
  }

  // 3. Fallback to YouTube oEmbed API if both fail
  try {
    const oembedUrl = `https://www.youtube.com/oembed?url=${encodeURIComponent(url)}&format=json`;
    const resp = await fetch(oembedUrl);
    if (!resp.ok) {
      throw new Error("Could not retrieve video details from YouTube.");
    }
    const data: any = await resp.json();
    return res.json({
      title: data.title || "YouTube Video",
      channel: data.author_name || "",
      thumbnail: data.thumbnail_url || "",
      duration: "0:00",
      heights: [1080, 720, 480, 360],
    });
  } catch (fallbackErr: any) {
    return res.status(400).json({
      error: "The server couldn't read that link. Please make sure the video is public and accessible.",
    });
  }
});

// GET /api/download
app.get("/api/download", async (req: Request, res: Response) => {
  const url = (req.query.url as string || "").trim();
  const isAudio = req.query.audio === "1";
  const heightStr = req.query.height as string;
  const targetHeight = heightStr ? parseInt(heightStr, 10) : undefined;

  if (!isValidYouTubeUrl(url)) {
    return res.status(400).send("Invalid URL");
  }

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "ytdl_"));
  const outTemplate = path.join(tmpDir, "%(title).150s.%(ext)s");

  try {
    const ydlOpts: any = {
      output: outTemplate,
      noPlaylist: true,
      quiet: true,
      noWarnings: true,
    };

    if (isAudio) {
      ydlOpts.format = "bestaudio/best";
      ydlOpts.extractAudio = true;
      ydlOpts.audioFormat = "mp3";
      ydlOpts.audioQuality = "192K";
    } else {
      const hFilter = targetHeight ? `[height<=${targetHeight}]` : "";
      ydlOpts.format = `bestvideo${hFilter}+bestaudio/best${hFilter}`;
      ydlOpts.mergeOutputFormat = "mp4";
    }

    await (ydlExec as any)(url, ydlOpts);

    const files = fs.readdirSync(tmpDir);
    if (files.length === 0) {
      throw new Error("No file was generated.");
    }

    const downloadedFile = path.join(tmpDir, files[0]);
    const fileName = path.basename(downloadedFile);

    res.download(downloadedFile, fileName, (err) => {
      try {
        fs.rmSync(tmpDir, { recursive: true, force: true });
      } catch (rmErr) {
        console.error("Cleanup error:", rmErr);
      }
      if (err && !res.headersSent) {
        res.status(500).send(`Download failed: ${err.message}`);
      }
    });
  } catch (execErr: any) {
    console.warn("youtube-dl-exec download failed, attempting ytdl stream fallback:", execErr?.message);

    // Fallback streaming with @distube/ytdl-core
    try {
      const info = await ytdl.getInfo(url);
      const rawTitle = info.videoDetails?.title || (isAudio ? "audio" : "video");
      const safeTitle = sanitizeFilename(rawTitle);

      if (isAudio) {
        res.setHeader("Content-Disposition", `attachment; filename="${safeTitle}.mp3"`);
        res.setHeader("Content-Type", "audio/mpeg");
        const stream = ytdl(url, { quality: "highestaudio", filter: "audioonly" });
        stream.pipe(res);
        stream.on("error", (err) => {
          console.error("Audio stream error:", err);
          if (!res.headersSent) res.status(500).send(`Stream error: ${err.message}`);
        });
      } else {
        res.setHeader("Content-Disposition", `attachment; filename="${safeTitle}.mp4"`);
        res.setHeader("Content-Type", "video/mp4");
        const stream = ytdl(url, { quality: "highestvideo", filter: "videoandaudio" });
        stream.pipe(res);
        stream.on("error", (err) => {
          console.error("Video stream error:", err);
          if (!res.headersSent) res.status(500).send(`Stream error: ${err.message}`);
        });
      }

      // Cleanup tmpDir on fallback
      try {
        fs.rmSync(tmpDir, { recursive: true, force: true });
      } catch {}
    } catch (fallbackErr: any) {
      try {
        fs.rmSync(tmpDir, { recursive: true, force: true });
      } catch {}
      return res.status(500).send(`Download failed: ${execErr?.message || fallbackErr?.message}`);
    }
  }
});

// Serve any static files in root (e.g. icons, css, etc.)
app.use(express.static(__dirname));

app.listen(PORT, HOST, () => {
  console.log(`Server running on http://${HOST}:${PORT}`);
});
