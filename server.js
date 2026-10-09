const express = require('express');
const { WebSocketServer } = require('ws');
const http = require('http');

const app = express();
const server = http.createServer(app);
const wss = new WebSocketServer({ server });

let waitingQueue = [];
const clients = new Map();

app.get('/', (req, res) => {
    res.send('WebSocket Signaling Server is running!');
});

wss.on('connection', (ws) => {
    console.log('Client terhubung via WebSocket.');

    ws.on('message', (message) => {
        let data;
        try {
            data = JSON.parse(message);
        } catch (e) {
            return;
        }

        switch (data.type) {
            case 'find':
                waitingQueue = waitingQueue.filter(item => item !== ws);
                if (waitingQueue.length > 0) {
                    const partner = waitingQueue.shift();
                    if (partner && partner.readyState === ws.OPEN) {
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

            case 'next':
                disconnectPartner(ws);
                waitingQueue = waitingQueue.filter(item => item !== ws);
                if (waitingQueue.length > 0) {
                    const partner = waitingQueue.shift();
                    if (partner && partner.readyState === ws.OPEN) {
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
                if (partner && partner.readyState === ws.OPEN) {
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
    });
});

function disconnectPartner(ws) {
    const partner = clients.get(ws);
    if (partner) {
        if (partner.readyState === ws.OPEN) {
            partner.send(JSON.stringify({ type: 'peer_disconnected' }));
        }
        clients.delete(partner);
        clients.delete(ws);
    }
}

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
    console.log(`Server berjalan di port ${PORT}`);
});
