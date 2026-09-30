from rest_framework import serializers

from routing.services.route_planner import SELECTORS


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=100)
    finish = serializers.CharField(max_length=100)
    optimize = serializers.ChoiceField(choices=list(SELECTORS), default="time")
    start_fuel_percent = serializers.IntegerField(min_value=0, max_value=100, default=100)
