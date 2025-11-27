# SPDX-FileCopyrightText: : 2023- The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT

import logging
import pandas as pd
import country_converter as coco

logger = logging.getLogger(__name__)


def custom_extra_functionality(n, snapshots, snakemake):
    config = snakemake.config
    
    if config.get("solving", {}).get("constraints",{}
    ).get(
        "firm_capacity", {}
    ).get("enable",False):
        add_Firm_Capacity_Constraint(
            n = n,
            capacity_shares = config["solving"]["constraints"]["firm_capacity"]["capacity_shares"],
            firm_share = config["solving"]["constraints"]["firm_capacity"]["max_demand_hour_share"],
        )
        
        
    if config.get("solving", {}).get("constraints",{}
    ).get(
        "import_tax", {}
    ).get("enable", False):
        add_electricity_trade_tax(
            n = n,
            tax_value = config["solving"]["constraints"]["import_tax"]["electricity"]["tax_value"],
            max_import_share = config["solving"]["constraints"]["import_tax"]["electricity"]["max_import_share"],
            
        )

        add_hydrogen_import_tax(
            n = n,
            max_import_share = config["solving"]["constraints"]["import_tax"]["hydrogen"]["max_import_share"],
            tax_value = config["solving"]["constraints"]["import_tax"]["hydrogen"]["tax_value"]
        )

    if config.get("solving", {}).get("constraints",{}
    ).get(
        "overwrite_technical_potentials", False
    ):
        renewable_technical_social_potential(
            n,        
            tyndp_path=config["solving"]["constraints"]["min_cap_constraint"]["path"],
            ratio = config["solving"]["constraints"]["min_cap_constraint"]["ratio"], 
        )
     
    if config.get("solving", {}).get("constraints",{}
    ).get(
        "oil_supply_constraint", {}
    ).get("enable", False):
        add_liquid_supply_constraint(
            n=n,
            supply_data_path=config["solving"]["constraints"]["oil_supply_constraint"]["path"],
        )
        
        # max_electrolyzer(n)
        
    # if config.get("solving", {}).get("constraints",{}
    # ).get(
    #     "min_cap_constraint", {}
    # ).get("enable", False):
    min_capacity_constraint_by_tyndp(
        n=n,
        tyndp_path=config["solving"]["constraints"]["min_cap_constraint"]["path"],
        ratio = config["solving"]["constraints"]["min_cap_constraint"]["ratio"],
        countries = config["solving"]["constraints"]["min_cap_constraint"]["countries"],
        nuclear = config["solving"]["constraints"]["min_cap_constraint"]["nuclear"],
    )    
    
    
    min_import_level(
        n = n,
        import_constraints=config["solving"]["constraints"]["import_constraints"]
    )

def get_tyndp_caps(tyndp_caps,ratio,tech_category_tyndp,country):

    return tyndp_caps.loc[
                    (tyndp_caps["loc"].str.startswith(dict(GB="UK").get(country,country))) &
                    (tyndp_caps["tech"].isin(tech_category_tyndp))
                ].energy_cap.sum() * ratio

def max_electrolyzer(n):
    logger.info(f"adding max electrilyzer constraint")
    links = n.links.loc[
        n.links.carrier == "H2 Electrolysis"
    ].index
    
    totcap = n.model["Link-p_nom"].sel({"Link-ext":links}).sum()
    n.model.add_constraints(totcap<=200000,name="max electrolyzers")
        
def get_carrier_capacity_at_country(n,buses,carrier):
    
    
    info_level = dict(links="bus1", lines="bus0",generators="bus",storage_units="bus",stores="bus")
    variable = dict(
        generators = ("Generator-p_nom","Generator-ext"),
        links = ("Link-p_nom","Link-ext"),
        stores = ("Store-e_nom","Store-ext"),
        storage_units = ("StorageUnit-p_nom","StorageUnit-ext")
    )
    t_map = {
            "OCGT": "links",
            "CCGT": "links",
            "OCGT methanol": "links",
            "nuclear": "generators",
            "H2 turbine": "links",
            "H2 Fuel Cell": "links",
            "hydro": "storage_units",
            "PHS": "storage_units",
            "battery": "stores",
            "home battery": "stores",
        }


    where = t_map[carrier]

    obj = getattr(n,where)

    if where == "stores":
        carriers = obj.loc[(obj.carrier == carrier) & (obj[info_level[where]].isin([f"{bus} {c}" for bus in buses for c in [carrier]]))]
        extend = "e_nom_extendable"
    else:
        carriers = obj.loc[(obj.carrier == carrier)&(obj[info_level[where]].isin(buses))]
        extend = "p_nom_extendable"


    lhs = 0
    if not carriers.empty:
        
        # extendable 
        extendable_carriers = carriers.loc[carriers[extend]].index
        non_extendable_carriers = carriers.loc[~carriers[extend]].index
        
        
        if len(extendable_carriers):
            lhs += n.model[variable[where][0]].sel({variable[where][1]: extendable_carriers})

        if len(non_extendable_carriers):
            lhs += carriers.loc[non_extendable_carriers, "p_nom"].sum()

    return lhs


def add_hydrogen_import_tax(n,max_import_share,tax_value):
    
    logger.info(f"adding import tax of {tax_value} on hydrogen with a maximum import limit of {max_import_share*100}%.")
    buses = n.loads.loc[n.loads.carrier.str.contains("H2")].index

    fix = n.loads.copy()
    fix = fix.loc[buses,"p_set"]
    fix.index = [x[:2] for x in fix.index]
    end_use_demand = fix.groupby(level=0).sum()*len(n.snapshots)


    # all hydrogen demand
    intermediate_carriers = n.links.loc[
        (n.links.bus0.str.contains("H2")) & (~n.links.carrier.str.contains("pipeline")) 
        ]

    country = n.buses.country.unique()
    objective = n.model.objective.expression
    links = n.links[n.links.index.str.contains("H2 pipeline")].copy()
    # 3. Add country codes for both ends
    links["country0"] = links["bus0"].str[:2]
    links["country1"] = links["bus1"].str[:2]

    for cc in country:
        if cc == "":
            continue

        country_buses = n.buses.loc[(n.buses.country == cc) & (n.buses.carrier == "AC")].index

        # p0
        exports = links.loc[
            (links["country0"] != links["country1"])
            & (links["country0"] == cc)
        ].index

        # p1
        imports = links.loc[
            (links["country0"] != links["country1"])
            & (links["country1"] == cc)
        ].index

        balance = n.model["Link-p"].sel({"Link":imports}).sum() - n.model["Link-p"].sel({"Link":exports}).sum()


        intermediate_demand_in_country = intermediate_carriers.loc[intermediate_carriers.index.str.startswith(cc)].index
        demand = end_use_demand.loc[cc] + n.model["Link-p"].sel({"Link":intermediate_demand_in_country}).sum()
        
        LimitBalance = balance - max_import_share*demand
        ProcessToBeTaxed = n.model.add_variables(lower=0, name=f"trade_tax_H2_{cc}")
        n.model.add_constraints(ProcessToBeTaxed>= LimitBalance, name=f"trade_tax_H2_{cc}_pos")
        objective+= ProcessToBeTaxed * tax_value

            
    n.model.add_objective(objective,overwrite=True)
            


def add_Firm_Capacity_Constraint(n,capacity_shares,firm_share):
    
    logger.info(f"Adding firm capacity constraint of {firm_share} at country level.")
    
    loads = n.loads_t.p
    buses = n.loads.loc[n.loads.carrier.str.contains("electric")].index


    profile = n.loads_t.p_set
    profile = profile[profile.columns.intersection(buses)]
    profile.columns = [x[:2] for x in profile.columns]

    fix = n.loads
    fix = fix.loc[buses.difference(profile.columns),"p_set"]
    fix.index = [x[:2] for x in fix.index]


    max_demand_hour = fix.groupby(level=0).sum() + profile.max()
    
    snapshot_weightings = n.snapshot_weightings.iloc[0,0]
    country = n.buses.country.unique()

    for bus in country:
        if bus == "":
            continue

        lhs = 0
        
        country_buses = n.buses.loc[(n.buses.country == bus) & (n.buses.carrier == "AC")].index
        rhs = max_demand_hour.loc[bus] * firm_share

        for carrier,share in capacity_shares.items():
            bus_capacity = get_carrier_capacity_at_country(n,country_buses,carrier)
            lhs += bus_capacity * share


        n.model.add_constraints(
            lhs >= rhs, name = f"firm_capacity_{bus}"
        )



def get_electricity_intermediate_demand(n,cc):

    intermedate_electricty_consumpition_carriers = [
        ("H2 Electrolysis","H2"),
        ("urban central air heat pump","urban central heat"),
        ("urban central resistive heater","urban central heat"),
        ("urban decentral air heat pump","urban decentral heat"),
        ("urban decentral resistive heater","urban decentral heat"),
        # "Haber-Bosch",
        ("DAC","urban central heat"),
        ("rural air heat pump","rural heat"),
        ("rural ground heat pump","rural heat"),
        ("rural resistive heater","rural heat"),
    ]

    var = n.model["Link-p"]


    # find out all the intermediate consumption in the country
    idx = n.links.loc[
        (n.links.carrier.isin([x[0] for x in intermedate_electricty_consumpition_carriers]))&
        (n.links.bus1.str.endswith(tuple([x[1] for x in intermedate_electricty_consumpition_carriers])))&
        (n.links.bus0.str.startswith(cc))
    ].index

    return var.sel({"Link":idx}).sum()

def add_electricity_trade_tax(n,tax_value,max_import_share):
    
    logger.info(f"adding import tax of {tax_value} on electricity with a maximum import limit of {max_import_share*100}%.")

    loads = n.loads_t.p
    buses = n.loads.loc[n.loads.carrier.str.contains("electric")].index


    profile = n.loads_t.p_set
    profile = profile[profile.columns.intersection(buses)]
    profile.columns = [x[:2] for x in profile.columns]

    fix = n.loads
    fix = fix.loc[buses.difference(profile.columns),"p_set"]
    fix.index = [x[:2] for x in fix.index]

    # since demand is the average value, there is no nead to multiply it with the weight of the snapshot
    annual_demand = (
        fix.groupby(level=0).sum()*len(n.snapshots) + profile.sum()
    ) 

    country = n.buses.country.unique()
    objective = n.model.objective.expression
    links = n.links[n.links.carrier == "DC"].copy()

    links["country0"] = links["bus0"].apply(lambda x: x[:2])
    links["country1"] = links["bus1"].apply(lambda x: x[:2])

    for cc in country:
        if cc == "":
            continue

        # p0
        exports = links.loc[
            (links["country0"] != links["country1"])
            & (links["country0"] == cc)
        ].index

        # p1
        imports = links.loc[
            (links["country0"] != links["country1"])
            & (links["country1"] == cc)
        ].index


        balance_DC = n.model["Link-p"].sel({"Link":imports}).sum() - n.model["Link-p"].sel({"Link":exports}).sum()
        
        # AC
        lines = n.lines[n.lines.carrier == "AC"]

        lines["country0"] = lines["bus0"].str[:2]
        lines["country1"] = lines["bus1"].str[:2]

        # p0
        exports = lines.loc[
            (lines["country0"] != lines["country1"])
            & (lines["country0"] == cc)
        ].index

        # p1
        imports = lines.loc[
            (lines["country0"] != lines["country1"])
            & (lines["country1"] == cc)
        ].index

        balance_AC = n.model["Line-s"].sel({"Line":imports}).sum() - n.model["Line-s"].sel({"Line":exports}).sum()

        balance = balance_DC + balance_AC

        total_demand = annual_demand.loc[cc].sum() + get_electricity_intermediate_demand(n,cc)
        LimitBlance = balance - max_import_share * total_demand
        
        ProcessToBeTaxed = n.model.add_variables(lower=0,name=f"trade_tax_electricity_{cc}")
        n.model.add_constraints(ProcessToBeTaxed>= LimitBlance, name=f"trade_tax_electricity_{cc}_pos")
        
        objective+= ProcessToBeTaxed * tax_value
            
            # read more carefully wouter email!

    n.model.add_objective(objective,overwrite=True)
    
    
def get_adjusted_potential(p_nom_max_jrc,p_nom_max_calliope,p_nom_min,threshold=3):

        _min = min(p_nom_max_jrc/p_nom_min,p_nom_max_calliope/p_nom_min)

        if _min >= threshold:
            p_nom_max = min(p_nom_max_jrc,p_nom_max_calliope)
        else:
            p_nom_max = max(p_nom_max_jrc,p_nom_max_calliope)

        return max(p_nom_max,p_nom_min)     

# later do this at regional level
def renewable_technical_social_potential(n,tyndp_path,ratio):
    
    logger.info(f"overriding the maximum technical potential of solar/onwind/offwind.")
    
    jrc_potentials = pd.read_csv("data/technical-social-potential/ENSPRESO_Integrated_NUTS2_Data.csv",index_col=0)*1000 #gw to mw
    jrc_potentials.index = [idx[0:2] for idx in jrc_potentials.index]
    jrc_potentials = jrc_potentials.groupby(level=0).sum()

    tyndp_caps = pd.read_csv(tyndp_path)


    calliope_potentials = pd.read_csv("data/technical-social-potential/capacities.csv",index_col=0).round(0)

    solar = [
        "solar","solar-hsat","solar rooftop"
    ]


    onwind = ["onwind"]

    offwind = [
        "offwind-ac", 
        "offwind-dc",
        "offwind-float",
    ] 

    conv = coco.CountryConverter()
    jrc_potentials.index = conv.pandas_convert(jrc_potentials.index.to_series(),to="ISO2")
    calliope_potentials.index = conv.pandas_convert(calliope_potentials.index.to_series(),to="ISO2")

    scenario = "medium"

    for country in jrc_potentials.index:
        # get solar utility
        country = dict(UK="GB",EL="GR").get(country,country)

        take = f"solar_capacity_gw_{scenario}_total"

        idx = n.generators.loc[
            (n.generators.index.str.startswith(country))&
            (n.generators.carrier.isin(solar))
        ].index



        if not idx.empty:
            try:
                p_nom_min = n.generators.loc[idx,"p_nom_min"].sum()
                p_nom_max_jrc = jrc_potentials.loc[country,take]
                p_nom_max_calliope = calliope_potentials.loc[country,["open_field_pv_mw","rooftop_pv_mw"]].sum()
                tyndp = get_tyndp_caps(tyndp_caps,ratio,["pv","dres_pv","pv_rete"],country)
                p_nom_max = get_adjusted_potential(p_nom_max_jrc,p_nom_max_calliope,p_nom_min)
                n.model.add_constraints( 
                    n.model["Generator-p_nom"].sel({"Generator-ext":idx}).sum() <= max(p_nom_max,tyndp),
                    name=f"realistic_solar_capacity_{country}"
                )

            except KeyError:
                pass

        # get onshore cap
        idx = n.generators.loc[
            (n.generators.index.str.startswith(country))&
            (n.generators.carrier.isin(onwind))
        ].index

        if not idx.empty:
            take = f"wind_onshore_capacity_gw_{scenario}"

            try:
                p_nom_min = n.generators.loc[idx,"p_nom_min"].sum()
                p_nom_max_jrc = jrc_potentials.loc[country,take]
                p_nom_max_calliope = calliope_potentials.loc[country,"onshore_wind_mw"]
                p_nom_max = get_adjusted_potential(p_nom_max_jrc,p_nom_max_calliope,p_nom_min)
                tyndp = get_tyndp_caps(tyndp_caps,ratio,["dres_wind_onshore","wind_onshore"],country)
                tyndp_max = get_tyndp_caps(tyndp_caps,2.5,["dres_wind_onshore","wind_onshore"],country) 
                n.model.add_constraints( 
                    n.model["Generator-p_nom"].sel({"Generator-ext":idx}).sum() <= min(max(p_nom_max,tyndp),tyndp_max),
                    name=f"realistic_onwind_capacity_{country}"
                )
            except KeyError:
                pass

        # offshore wind
        idx = n.generators.loc[
            (n.generators.index.str.startswith(country))&
            (n.generators.carrier.isin(offwind))
        ].index

        if not idx.empty:
            try:
                p_nom_min = n.generators.loc[idx,"p_nom_min"].sum()
                p_nom_max_calliope = calliope_potentials.loc[country,"offshore_wind_mw"]
                p_nom_max = get_adjusted_potential(0,p_nom_max_calliope,p_nom_min)
                tyndp = get_tyndp_caps(tyndp_caps,ratio,["ac_fix_based_offshore_radial","ac_floating_offshore_radial","dc_fix_based_offshore_hub","dc_fix_based_offshore_radial","dc_floating_offshore_hub","dc_floating_offshore_radial"],country)
                             
                n.model.add_constraints( 
                    n.model["Generator-p_nom"].sel({"Generator-ext":idx}).sum() <= max(p_nom_max,tyndp),
                    name=f"realistic_offwind_capacity_{country}"
                )

            except KeyError:
                pass

def add_liquid_supply_constraint(n,supply_data_path,):
    # add supply constraint over oil products

    timesteps = n.snapshot_weightings.iloc[0,0]
    logger.info(f"adding liquid supply global constraint from file: {supply_data_path}.")
    supply_data = pd.read_csv(supply_data_path,index_col=1) #TWh to MWh and splited over timesteps

    e_fuel = (["Fischer-Tropsch"],["e_diesel","e_kerosene"])
    biofuel = (["biomass to liquid","electrobiofuels","biomass to liquid CC"],["biofuel"])
    fossil = (["oil refining"],[])

    efficiency = {
        "biofuel": 0.45,
        "e_fuel": 0.799,
    }
    for name,info in dict(e_fuel=e_fuel,biofuel=biofuel).items():

        category,constraint = info

        if constraint:
            
            logger.info(f"--> {category}: {supply_data[constraint].sum().sum()*1000000}. --> {timesteps}")
            
            links = n.links.loc[n.links.carrier.isin(category)].index

            prod = n.model["Link-p"].sel({"Link":links}).sum()

            n.model.add_constraints(
                prod*timesteps >= supply_data[constraint].sum().sum()*1000000/efficiency[name]*0.9,
                name = "min_prod_{}".format(name)
            )
            n.model.add_constraints(
                prod*timesteps <= supply_data[constraint].sum().sum()*1000000/efficiency[name]*1.1,
                name = "xmin_prod_{}".format(name)
            )
    
    # # oil refining
    # idx = n.links.loc[n.links.carrier.isin(["oil refining"])].index
    # prod = n.model["Link-p"].sel({"Link":idx})
    # logger.info(f"--> oil refining: {371*1000000}.")
    # n.model.add_constraints(
    #     prod == 371*1000000/8760,
    #     name = "equal_prod_fossil_oil"
    # )
            
def min_capacity_constraint_by_tyndp(n,ratio,tyndp_path,countries,nuclear):
    
    logger.info(f"adding minimum capacity conctraints accordign to tyndp24 scenario results by a ratio of {ratio}, loaded from {tyndp_path}.")
    if nuclear:
        logger.info(f"adding the capacity of nuclear as the max(p_nom,tyndp24).")
    
    tyndp_caps = pd.read_csv(tyndp_path)

    solar = (["solar","solar-hsat","solar rooftop"],["pv","dres_pv","pv_rete"])

    onwind = (["onwind"],["dres_wind_onshore","wind_onshore"])


    offwind = (
        ["offwind-ac", "offwind-dc", "offwind-float"], ["ac_fix_based_offshore_radial","ac_floating_offshore_radial","dc_fix_based_offshore_hub","dc_fix_based_offshore_radial","dc_floating_offshore_hub","dc_floating_offshore_radial"]
    ) 
    
    
    if countries == "all":
        countries = n.buses.country.unique()
        
    for country in countries:

        if country == "":
            continue

        for tech_category_pypsa,tech_category_tyndp in [
            solar,
            onwind,
            offwind
            ]:

            pypsa_idx = n.generators.loc[
                (n.generators.index.str.startswith(country))&
                (n.generators.carrier.isin(tech_category_pypsa))
            ].index     

            tyndp_cap_idx = get_tyndp_caps(tyndp_caps,ratio,tech_category_tyndp,country)

            pypsa_cap_max = n.generators.loc[pypsa_idx,"p_nom_max"].sum()

            if not pypsa_idx.empty  and tyndp_cap_idx != 0:
                var = n.model["Generator-p_nom"].sel({"Generator-ext":pypsa_idx}).sum()
                lower_bound = tyndp_cap_idx * ratio

                if lower_bound > pypsa_cap_max:
                    lower_bound = pypsa_cap_max

                
                
                n.model.add_constraints(
                    var >= lower_bound,
                    name = "tyndp_min_"+",".join(tech_category_pypsa) + "_" + country
                )
                
                if country == "NO" and tech_category_pypsa[0].startswith("off"):
                    n.model.add_constraints(
                        var >= 30000,
                        name = "min offshorewind - Norway"
                    )
                
        if nuclear:
            
            tyndp_cap = tyndp_caps.loc[
                (tyndp_caps["loc"] == dict(GB="UK").get(country,country)) &
                (tyndp_caps["tech"] == "nuclear_pp")
            ].energy_cap.sum()

            pypsa_cap = n.generators.loc[
            (n.generators.carrier == "nuclear") & (n.generators.index.str.startswith(country))
            ].p_nom.sum()

            constraint = max(tyndp_cap,pypsa_cap)

            idx = n.generators.loc[
                (n.generators.carrier == "nuclear") & (n.generators.index.str.startswith(country))
            ].index

            n.model.add_constraints(
                n.model["Generator-p_nom"].sel({"Generator-ext":idx}).sum() == constraint,
                name = "nuclear constraint in {}".format(country)
            )
            
    

            
            
def min_import_level(n,import_constraints):
    carriers = import_constraints["carriers"]
    sense = import_constraints["sense"]
    
    
    
    for carrier,val in carriers.items():
        if carrier in ["NH3","H2"]: continue
        constraint = val/8760
        carrier_map = f"import {carrier}"
        if carrier_map in n.generators.carrier.unique():
            logger.info(f"adding constraint on import of {carrier_map} {sense} {constraint}")
            idx = n.generators.loc[
                    n.generators.carrier == carrier_map
                ].index
            var = n.model["Generator-p"].sel({"Generator":idx})
            
            n.model.add_constraints(
                eval(f"var {sense} constraint"),
                name = f"{carrier_map} {sense} {constraint}"
            )

        if carrier_map in n.links.carrier.unique():
            logger.info(f"adding constraint on import of {carrier_map} {sense} {constraint}")
            idx = n.links.loc[
                    n.links.carrier == carrier_map
                ].index
            
            eff = n.links.loc[
                n.links.carrier == carrier_map
            ].efficiency
            var = n.model["Link-p"].sel({"Link":idx})

            n.model.add_constraints(
                eval(f"var {sense} constraint/eff"),
                name = f"{carrier_map} {sense} {val}"
            )

        else:
            continue
    
    constraint = pd.read_csv(carriers["NH3"])
    NH3 = constraint.loc[constraint.carrier == "NH3"]

    for node,carrier,value in NH3.itertuples(index=False):
        logger.info(f"adding constraint on import of {carrier}-{node} {value}")
        idx = n.links.loc[
            (n.links.carrier == "ammonia cracker") &
            (n.links.index.str.startswith(node))
        ]

        if idx.empty:
            print(f"{node} {carrier} does not exist")

        else:
            var = n.model["Link-p"].sel({"Link":idx.index})
            n.model.add_constraints(
                var >= value/8760*1.46*0.9,name=f"NH3 import in {node}" #FIXME Avoid hardcoding eff number
                )
            var = n.model["Link-p"].sel({"Link":idx.index})
            n.model.add_constraints(
                var <= value/8760*1.46*1.1,name=f"xNH3 import in {node}" #FIXME Avoid hardcoding eff number
                )
    #%%
    H2 = constraint.loc[constraint.carrier == "H2"]
    
    all_nodes = tuple(H2.node.tolist())

    idx = n.generators.loc[
        (n.generators.carrier == "import H2") &
        (~n.generators.index.str.startswith(all_nodes))
    ].index

    var = n.model["Generator-p_nom"].sel({"Generator-ext":idx})
    n.model.add_constraints(
        var == 0
    )
    

    for node,carrier,value in H2.itertuples(index=False):
        
        logger.info(f"adding constraint on import of {carrier}-{node} {value}")

        idx = n.generators.loc[
            (n.generators.carrier == "import H2") &
            (n.generators.index.str.startswith(node))
        ]

        if idx.empty:
            print(f"{node} {carrier} does not exist")

        else:
            var = n.model["Generator-p"].sel({"Generator":idx.index})
            n.model.add_constraints(
                var <= value/8760*1.1,name=f"H2 import in {node}y"
                )
            
            var = n.model["Generator-p"].sel({"Generator":idx.index})
            n.model.add_constraints(
                var >= value/8760*0.9,name=f"xH2 import in {node}y"
                )
            
