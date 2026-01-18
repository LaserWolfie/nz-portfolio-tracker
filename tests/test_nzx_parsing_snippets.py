from src.services.market_data import parse_nzx_instrument_html


EBO_HTML = """\
<html>
  <body>
    <script id="__NEXT_DATA__" type="application/json">
      {"props":{"pageProps":{"overview":{"lastPrice":"26.4600"}}}}
    </script>
    <table>
      <tr><th>Securities Issued</th><td>205,025,626</td></tr>
    </table>
  </body>
</html>
"""


IFT_HTML = """\
<html>
  <body>
    <script id="__NEXT_DATA__" type="application/json">
      {"props":{"pageProps":{"overview":{"lastPrice":null,"priceAmount":"5.3600"}}}}
    </script>
    <table>
      <tr><th>Securities Issued</th><td>999,312,470</td></tr>
    </table>
  </body>
</html>
"""


def test_nzx_snippets_parse_price_and_shares():
    ebo = parse_nzx_instrument_html(EBO_HTML)
    assert ebo["price"] == 26.46
    assert ebo["shares_outstanding"] == 205_025_626.0

    ift = parse_nzx_instrument_html(IFT_HTML)
    assert ift["price"] == 5.36
    assert ift["shares_outstanding"] == 999_312_470.0
