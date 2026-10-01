from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from routing.serializers import RouteRequestSerializer
from routing.services.fuel_optimizer import NoFeasiblePlan
from routing.services.geocoding import LocationNotFound
from routing.services.osrm import RoutingError
from routing.services.route_planner import plan_trip

ERROR_STATUS = {
    LocationNotFound: status.HTTP_400_BAD_REQUEST,
    NoFeasiblePlan: status.HTTP_422_UNPROCESSABLE_ENTITY,
    RoutingError: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def run_plan(data):
    serializer = RouteRequestSerializer(data=data)
    if not serializer.is_valid():
        return None, serializer.errors, status.HTTP_400_BAD_REQUEST
    params = serializer.validated_data
    try:
        return plan_trip(params["start"], params["finish"], params["optimize"], params["start_fuel_percent"]), None, status.HTTP_200_OK
    except tuple(ERROR_STATUS) as exc:
        return None, {"error": str(exc)}, ERROR_STATUS[type(exc)]


class RoutePlanView(APIView):
    def post(self, request):
        plan, errors, code = run_plan(request.data)
        if plan is None:
            return Response(errors, status=code)
        query = urlencode({"start": plan["start"]["query"], "finish": plan["finish"]["query"], "optimize": plan["optimize"],
                           "start_fuel_percent": plan["start_fuel_percent"]})
        return Response({"map_url": request.build_absolute_uri(f"{reverse('route-map')}?{query}"), **plan})


def route_map(request):
    plan, errors, code = run_plan(request.GET)
    return render(request, "routing/map.html", {"plan": plan, "errors": errors}, status=code)
