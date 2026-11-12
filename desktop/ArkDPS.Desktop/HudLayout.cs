using System.Text.Json;
using System.Text.Json.Serialization;

namespace ArkDPS.Desktop;

internal sealed record HudLayout(
    [property: JsonPropertyName("title_font")] double TitleFont=28,
    [property: JsonPropertyName("name_font")] double NameFont=20,
    [property: JsonPropertyName("number_font")] double NumberFont=18,
    [property: JsonPropertyName("width")] double Width=568,
    [property: JsonPropertyName("row_height")] double RowHeight=46,
    [property: JsonPropertyName("title_height")] double TitleHeight=44,
    [property: JsonPropertyName("portrait_size")] double PortraitSize=36,
    [property: JsonPropertyName("rank_width")] double RankWidth=36,
    [property: JsonPropertyName("portrait_width")] double PortraitWidth=44,
    [property: JsonPropertyName("name_width")] double NameWidth=140,
    [property: JsonPropertyName("bar_width")] double BarWidth=42,
    [property: JsonPropertyName("total_width")] double TotalWidth=94,
    [property: JsonPropertyName("rate_width")] double RateWidth=104,
    [property: JsonPropertyName("share_width")] double ShareWidth=76,
    [property: JsonPropertyName("row_gap")] double RowGap=0,
    [property: JsonPropertyName("column_gap")] double ColumnGap=4,
    [property: JsonPropertyName("padding")] double Padding=4)
{
    internal const double DefaultRowHeight=46;
    internal static HudLayout Default { get; }=new();
    internal double RowPitch => RowHeight+RowGap;

    internal static HudLayout FromJson(JsonElement style)
    {
        if(style.ValueKind!=JsonValueKind.Object) return Default;
        try { return style.Deserialize<HudLayout>()??Default; }
        catch(JsonException) { return Default; }
    }
}
