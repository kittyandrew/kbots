# I hate
template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Losses Report</title>
    <link href="https://fonts.googleapis.com/css2?family=Tinos:ital,wght@0,300;0,400;0,700;1,300;1,400;1,700&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: Tinos;
        }
        h2 {
            text-align: center;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            white-space: nowrap;
        }
        th, td {
            border: 1px solid black;
            padding: 4px;
        }
        .numeration {
            background-color: #c5e0b3;
            text-align: center;
        }
        td {
            background-color: #ffffff;
            padding-top: 1px; 
            padding-bottom: 1px; 
            margin-top: 1px; 
            margin-bottom: 1px; 
        }
        .russian-header {
            background-color: #f7caac;
            text-align: center;
            font-size: 20px;
        }
        .ukrainian-header {
            background-color: #ffe598;
            text-align: center;
            font-size: 20px;
        }
        .content-items {
            text-align: left;
            font-weight: 300;
        }
        .center-align {
            text-align: center;
        }
        .total-color {
            background-color: #b4c6e7;
        }
        .total {
            font-weight: bold;
            text-align: center;
        }
    </style>
</head>
<body>

<h2> {{ date }} &nbsp; https://t.me/lost_warinua </h2>
<table>
    <tr>
        <th class="numeration">№</th>
        <th class="russian-header">Russian losses</th>
        <th class="ukrainian-header">Ukrainian losses</th>
    </tr>
    {% for left, right in losses %}
        <tr>
            <td class="numeration">{{ loop.index }}</td>
            <td class="content-items">{{ left }}&ensp;</td>
            <td class="content-items">{{ right }}&ensp;</td>
        </tr>
    {% endfor %}
    <tr>
        <td>&nbsp;</td>
        <td>&nbsp;</td>
        <td>&nbsp;</td>
    </tr>
    <tr class="total">
        <td class="total-color">Total:</td>
        <td class="center-align">{{ ru_total }}</td>
        <td class="center-align">{{ ua_total }}</td>
    </tr>
</table>

</body>
</html>
"""

if __name__ == "__main__":
    with open("template.html", "w+") as f:
        f.write(template)
