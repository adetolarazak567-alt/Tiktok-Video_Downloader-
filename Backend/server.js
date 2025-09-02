// server.js
const express = require('express');
const cors = require('cors');
const { spawn } = require('child_process');

const app = express();

app.use(cors());
app.use(express.json());

// Get video info (title, thumbnail, etc.)
app.post('/download', (req, res) => {
  const { url } = req.body;
  if (!url) return res.status(400).json({ error: 'No URL provided' });

  // Use yt-dlp to fetch metadata
  const ytdlpProcess = spawn('yt-dlp', ['-j', url]);

  let output = '';
  ytdlpProcess.stdout.on('data', (data) => {
    output += data.toString();
  });

  ytdlpProcess.on('close', (code) => {
    if (code !== 0) {
      console.error(`yt-dlp exited with code ${code}`);
      return res.status(500).json({ error: 'Error fetching video' });
    }
    try {
      const info = JSON.parse(output);
      res.json({
        title: info.title,
        thumbnail: info.thumbnail,
        streamUrl: `/stream?url=${encodeURIComponent(url)}`
      });
    } catch (err) {
      console.error(err);
      res.status(500).json({ error: 'Error parsing video info' });
    }
  });
});

// Stream video through backend (fixes 403 Forbidden)
app.get('/stream', (req, res) => {
  const url = req.query.url;
  if (!url) return res.status(400).send('No URL provided');

  res.setHeader('Content-Type', 'video/mp4');

  const ytdlpProcess = spawn('yt-dlp', ['-o', '-', url]);

  ytdlpProcess.stdout.pipe(res);

  ytdlpProcess.stderr.on('data', (data) => {
    console.error(`yt-dlp error: ${data}`);
  });

  ytdlpProcess.on('close', (code) => {
    if (code !== 0) {
      console.error(`yt-dlp exited with code ${code}`);
      res.end();
    }
  });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
