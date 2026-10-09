const WebSocket = require('ws');
const http = require('http');
const express = require('express');

const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

let waitingQueue = [];
const clients = new Map(); // ws -> partner_ws

wss.on('connection', (ws) => {
    console.log('Client terhubung.');

    ws.on('message', (message) => {
        let data;
        try {
            data = JSON.parse(message);
        } catch (e) {
            return;
        }

        switch (data.type) {
            case 'find':
                // Hapus dari antrean jika sudah ada sebelumnya
                waitingQueue = waitingQueue.filter(item => item !== ws);

                if (waitingQueue.length > 0) {
                    // Ambil partner dari antrean terdepan
                    const partner = waitingQueue.shift();

                    if (partner && partner.readyState === WebSocket.OPEN) {
                        clients.set(ws, partner);
                        clients.set(partner, ws);

                        // Kirim sinyal match (satu sebagai caller, satu sebagai callee)
                        ws.send(JSON.stringify({ type: 'matched', role: 'caller' }));
                        partner.send(JSON.stringify({ type: 'matched', role: 'callee' }));
                        console.log('Pasangan ditemukan dan dihubungkan!');
                    } else {
                        waitingQueue.push(ws);
                        ws.send(JSON.stringify({ type: 'waiting' }));
                    }
                } else {
                    waitingQueue.push(ws);
                    ws.send(JSON.stringify({ type: 'waiting' }));
                    console.log('User masuk antrean tunggu.');
                }
                break;

            case 'next':
                disconnectPartner(ws);
                // Masukkan kembali ke antrean pencarian
                waitingQueue = waitingQueue.filter(item => item !== ws);
                if (waitingQueue.length > 0) {
                    const partner = waitingQueue.shift();
                    if (partner && partner.readyState === WebSocket.OPEN) {
                        clients.set(ws, partner);
                        clients.set(partner, ws);
                        ws.send(JSON.stringify({ type: 'matched', role: 'caller' }));
                        partner.send(JSON.stringify({ type: 'matched', role: 'callee' }));
                    } else {
                        waitingQueue.push(ws);
                        ws.send(JSON.stringify({ type: 'waiting' }));
                    }
                } else {
                    waitingQueue.push(ws);
                    ws.send(JSON.stringify({ type: 'waiting' }));
                }
                break;

            case 'offer':
            case 'answer':
            case 'candidate':
                const partner = clients.get(ws);
                if (partner && partner.readyState === WebSocket.OPEN) {
                    partner.send(JSON.stringify(data));
                }
                break;

            case 'hangup':
                disconnectPartner(ws);
                ws.send(JSON.stringify({ type: 'ended' }));
                break;
        }
    });

    ws.on('close', () => {
        waitingQueue = waitingQueue.filter(item => item !== ws);
        disconnectPartner(ws);
        console.log('Client terputus.');
    });
});

function disconnectPartner(ws) {
    const partner = clients.get(ws);
    if (partner) {
        if (partner.readyState === WebSocket.OPEN) {
            partner.send(JSON.stringify({ type: 'peer_disconnected' }));
        }
        clients.delete(partner);
        clients.delete(ws);
    }
}

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
    console.log(`Signaling server aktif di port ${PORT}`);
});
