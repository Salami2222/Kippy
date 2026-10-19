import unittest

from macromellow.umd import label_url, parse_label, parse_menu


class UMDParsingTests(unittest.TestCase):
    def test_menu_keeps_hall_page_meal_and_station_context(self):
        html = """
        <a role="tab" aria-controls="pane-1">Breakfast</a>
        <a role="tab" aria-controls="pane-2">Lunch</a>
        <div class="tab-pane" id="pane-1"><h3 class="card-title">Broiler Works</h3>
        <a class="menu-item-name" href="label.aspx?RecNumAndPort=119370*1">French Toast</a></div>
        <div class="tab-pane" id="pane-2"><h3 class="card-title">Salad Bar</h3>
        <a class="menu-item-name" href="label.aspx?RecNumAndPort=111111*1">Apple</a></div>
        """
        self.assertEqual(parse_menu(html), [
            {"name": "French Toast", "meal": "Breakfast", "station": "Broiler Works", "href": "label.aspx?RecNumAndPort=119370*1"},
            {"name": "Apple", "meal": "Lunch", "station": "Salad Bar", "href": "label.aspx?RecNumAndPort=111111*1"},
        ])

    def test_label_uses_primary_facts_not_daily_values_or_secondary_list(self):
        html = """
        <h1>French Toast</h1>
        <div class="nutfactsservsize">Serving size</div><div class="nutfactsservsize">1 ea</div>
        <p>Calories per serving</p><p>251</p>
        <span class="nutfactstopnutrient"><b>Total Fat&nbsp;</b>10.5g</span>
        <span class="nutfactstopnutrient"><b>Total Carbohydrate.&nbsp;</b>26g</span>
        <span class="nutfactstopnutrient"><b>20%</b></span>
        <span class="nutfactstopnutrient"><b>Protein&nbsp;</b>10.9g</span>
        <span class="nutfactstopnutrient">Calories&nbsp;250.8kcal 42%</span>
        """
        self.assertEqual(parse_label(html), {
            "serving_size": "1 ea", "calories": 251.0, "fat_g": 10.5,
            "carbohydrates_g": 26.0, "protein_g": 10.9,
        })

    def test_missing_fields_remain_null(self):
        self.assertEqual(parse_label("<h1>Unknown food</h1>"), {
            "serving_size": None, "calories": None, "fat_g": None,
            "carbohydrates_g": None, "protein_g": None,
        })

    def test_unescaped_space_in_official_label_link_is_encoded(self):
        self.assertEqual(
            label_url("label.aspx?RecNumAndPort=040065*4 1/2"),
            "https://nutrition.umd.edu/label.aspx?RecNumAndPort=040065%2A4%201%2F2",
        )


if __name__ == "__main__":
    unittest.main()
