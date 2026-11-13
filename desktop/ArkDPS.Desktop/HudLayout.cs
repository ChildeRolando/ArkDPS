using System.Text.Json;
using System.Text.Json.Serialization;

namespace ArkDPS.Desktop;

internal sealed record HudLayout(
    [property: JsonPropertyName("title_font")] double TitleFont=20,
    [property: JsonPropertyName("name_font")] double NameFont=15,
    [property: JsonPropertyName("number_font")] double NumberFont=15,
    [property: JsonPropertyName("width")] double Width=484,
    [property: JsonPropertyName("row_height")] double RowHeight=46,
    [property: JsonPropertyName("title_height")] double TitleHeight=44,
    [property: JsonPropertyName("portrait_size")] double PortraitSize=38,
    [property: JsonPropertyName("rank_width")] double RankWidth=28,
    [property: JsonPropertyName("portrait_width")] double PortraitWidth=52,
    [property: JsonPropertyName("name_width")] double NameWidth=92,
    [property: JsonPropertyName("bar_width")] double BarWidth=158,
    [property: JsonPropertyName("total_width")] double TotalWidth=78,
    [property: JsonPropertyName("rate_width")] double RateWidth=76,
    [property: JsonPropertyName("share_width")] double ShareWidth=68,
    [property: JsonPropertyName("row_gap")] double RowGap=1,
    [property: JsonPropertyName("column_gap")] double ColumnGap=2,
    [property: JsonPropertyName("padding")] double Padding=4,
    [property: JsonPropertyName("scale")] double Scale=1)
{
    internal const double DefaultRowHeight=46;
    internal const double MinimumScale=0.5;
    internal const double MaximumScale=2;
    internal static HudLayout Default { get; }=new();
    internal double RowPitch => RowHeight+RowGap;

    internal static HudLayout FromJson(JsonElement style)
    {
        if(style.ValueKind!=JsonValueKind.Object) return Default;
        try
        {
            var layout=style.Deserialize<HudLayout>()??Default;
            return double.IsFinite(layout.Scale) && layout.Scale is >=MinimumScale and <=MaximumScale
                ? layout : layout with {Scale=Default.Scale};
        }
        catch(JsonException) { return Default; }
    }
}
