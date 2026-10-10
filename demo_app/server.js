/**
 * HealTest Demo App — three revisions in one server
 *
 * REVISION env var controls behaviour:
 *   a (default) — Working: "Checkout" button, order confirmation shown
 *   b           — UI drift: button renamed to "Complete Order", flow still works
 *   c           — Real failure: /api/checkout returns 500
 *
 * Usage:
 *   npm start           (revision a)
 *   REVISION=b npm start
 *   REVISION=c npm start
 */

const express = require('express');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;
const REVISION = (process.env.REVISION || 'a').toLowerCase();

app.use(express.json());

// ── Checkout API ──────────────────────────────────────────────────────────────

app.post('/api/checkout', (req, res) => {
  if (REVISION === 'c') {
    // Revision C: deliberate server error
    return res.status(500).json({ error: 'Internal server error', code: 'PAYMENT_GATEWAY_DOWN' });
  }
  // Revisions A and B: success
  const orderId = `ORD-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
  res.json({ success: true, orderId, message: 'Order confirmed' });
});

// ── Health check ──────────────────────────────────────────────────────────────

app.get('/health', (_req, res) => res.json({ status: 'ok', revision: REVISION }));

// ── Serve HTML page ───────────────────────────────────────────────────────────

app.get('/', (_req, res) => {
  // Button label differs between revisions A and B
  const buttonLabel = REVISION === 'b' ? 'Complete Order' : 'Checkout';
  const buttonId = REVISION === 'b' ? 'complete-order-btn' : 'checkout-btn';

  res.send(`<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HealTest Demo Store</title>
  <style>
    body { font-family: sans-serif; max-width: 600px; margin: 60px auto; padding: 0 20px; }
    h1 { color: #333; }
    .cart { border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin: 20px 0; }
    button { background: #4CAF50; color: white; border: none; padding: 12px 24px;
             font-size: 16px; border-radius: 4px; cursor: pointer; }
    button:hover { background: #45a049; }
    button:disabled { background: #ccc; cursor: default; }
    #status { margin-top: 20px; padding: 12px; border-radius: 4px; display: none; }
    #status.success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
    #status.error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
  </style>
</head>
<body>
  <h1>Demo Store</h1>
  <p>Revision: <strong>${REVISION.toUpperCase()}</strong></p>

  <div class="cart">
    <h2>Shopping Cart</h2>
    <p>Widget Pro × 1 — $29.99</p>
    <p><strong>Total: $29.99</strong></p>
  </div>

  <button id="${buttonId}" role="button">${buttonLabel}</button>

  <div id="status"></div>

  <script>
    const btn = document.getElementById('${buttonId}');
    const status = document.getElementById('status');

    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = 'Processing…';
      status.style.display = 'none';

      try {
        const resp = await fetch('/api/checkout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cartId: 'cart-001' }),
        });
        const data = await resp.json();

        if (resp.ok && data.success) {
          status.className = 'success';
          status.textContent = 'Order confirmed — ' + data.orderId;
        } else {
          status.className = 'error';
          status.textContent = 'Checkout failed: ' + (data.error || 'Unknown error');
        }
      } catch (err) {
        status.className = 'error';
        status.textContent = 'Network error: ' + err.message;
      }

      status.style.display = 'block';
      btn.disabled = false;
      btn.textContent = '${buttonLabel}';
    });
  </script>
</body>
</html>`);
});

// ── Start ─────────────────────────────────────────────────────────────────────

app.listen(PORT, () => {
  console.log(\`HealTest demo app (revision \${REVISION.toUpperCase()}) running on http://localhost:\${PORT}\`);
});

module.exports = app;
