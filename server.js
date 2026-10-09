const express = require('express');
const { WebSocketServer } = require('ws');
const http = require('http');

const app = express();
const server = http.createServer(app);
const wss = new WebSocketServer({ server });

app.use(express.static('public'));

let waitingUser = null;

wss.on('connection', (ws) => {
    ws.on('message', (message) => {
        let data;
        try {
            data = JSON.parse(message);
        } catch (e) {
            return;
        }

        if (data.type === 'join') {
            if (waitingUser && waitingUser !== ws && waitingUser.readyState === ws.OPEN) {
                // Pasangkan pengguna
                ws.partner = waitingUser;
                waitingUser.partner = ws;

                ws.send(JSON.stringify({ type: 'matched' }));
                waitingUser.send(JSON.stringify({ type: 'matched' }));

                waitingUser = null;
            } else {
                waitingUser = ws;
                ws.send(JSON.stringify({ type: 'waiting' }));
            }
        } else if (data.type === 'signal') {
            if (ws.partner && ws.partner.readyState === ws.OPEN) {
                ws.partner.send(JSON.stringify({ type: 'signal', data: data.data }));
            }
        } else if (data.type === 'skip') {
            if (ws.partner) {
                ws.partner.send(JSON.stringify({ type: 'partner_skipped' }));
                ws.partner.partner = null;
                ws.partner = null;
            }
            if (waitingUser === ws) {
                waitingUser = null;
            }
            waitingUser = ws;
            ws.send(JSON.stringify({ type: 'waiting' }));
        }
    });

    ws.on('close', () => {
        if (ws.partner && ws.partner.readyState === ws.OPEN) {
            ws.partner.send(JSON.stringify({ type: 'partner_disconnected' }));
            ws.partner.partner = null;
        }
        if (waitingUser === ws) {
            waitingUser = null;
        }
    });
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
    console.log(`Server WebSocket berjalan di port ${PORT}`);
});
