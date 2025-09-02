// server.js
const express = require('express');
const cors = require('cors');
const ytdlp = require('youtube-dl-exec');
const app = express();

app.use(cors());
app.use(express.json());

app.post('/download', async (req, res) => {
  const { url } = req.body;
  if (!url) return res.status(400).json({ error: 'No URL provided' });

  try {
    const info = await ytdlp(url, { dumpJson: true });
    if (!info || !info.url) return res.status(500).json({ error: 'Video not found' });
    res.json({ videoUrl: info.url });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'Error fetching video' });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
