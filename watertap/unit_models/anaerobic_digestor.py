#################################################################################
# WaterTAP Copyright (c) 2020-2023, The Regents of the University of California,
# through Lawrence Berkeley National Laboratory, Oak Ridge National Laboratory,
# National Renewable Energy Laboratory, and National Energy Technology
# Laboratory (subject to receipt of any required approvals from the U.S. Dept.
# of Energy). All rights reserved.
#
# Please see the files COPYRIGHT.md and LICENSE.md for full copyright and license
# information, respectively. These files are also available online at the URL
# "https://github.com/watertap-org/watertap/"
#################################################################################
"""
Modified CSTR model which includes vapor and liquid phase outlets.

This is copied from the standard IDAES CSTR with the addition of mass transfer
terms and extra port for second phase.

Assumptions:
     * Steady-state only
     * Liquid phase property package has a single phase named Liq
     * Vapor phase property package has a single phase named Vap
     * Liquid and vapor phase properties need not have the same component lists

Model formulated from:

Rosen, C. and Jeppsson, U., 2006.
Aspects on ADM1 Implementation within the BSM2 Framework.
Department of Industrial Electrical Engineering and Automation, Lund University, Lund, Sweden, pp.1-35.
"""

# Import Pyomo libraries
from pyomo.common.config import ConfigBlock, ConfigValue, In, Bool
from pyomo.environ import (
    Reference,
    Var,
    value,
    Constraint,
    Param,
    units as pyunits,
    check_optimal_termination,
    exp,
    Suffix,
)


# Import IDAES cores
from idaes.core import (
    ControlVolume0DBlock,
    declare_process_block_class,
    MaterialBalanceType,
    EnergyBalanceType,
    MaterialFlowBasis,
    MomentumBalanceType,
    UnitModelBlockData,
    useDefault,
)
from idaes.core.util.config import (
    is_physical_parameter_block,
    is_reaction_parameter_block,
)

import idaes.logger as idaeslog
from idaes.core.util import scaling as iscale
from idaes.core.solvers import get_solver
from idaes.core.util.model_statistics import degrees_of_freedom
from idaes.core.util.constants import Constants
from idaes.core.util.exceptions import ConfigurationError, InitializationError
from idaes.core.util.tables import create_stream_table_dataframe

__author__ = "Alejandro Garciadiego, Andrew Lee"


@declare_process_block_class("AD")
class ADData(UnitModelBlockData):
    """
    AD Unit Model Class
    """

    CONFIG = UnitModelBlockData.CONFIG()

    CONFIG.declare(
        "material_balance_type",
        ConfigValue(
            default=MaterialBalanceType.useDefault,
            domain=In(MaterialBalanceType),
            description="Material balance construction flag",
            doc="""Indicates what type of mass balance should be constructed,
**default** - MaterialBalanceType.useDefault.
**Valid values:** {
**MaterialBalanceType.useDefault - refer to property package for default
balance type
**MaterialBalanceType.none** - exclude material balances,
**MaterialBalanceType.componentPhase** - use phase component balances,
**MaterialBalanceType.componentTotal** - use total component balances,
**MaterialBalanceType.elementTotal** - use total element balances,
**MaterialBalanceType.total** - use total material balance.}""",
        ),
    )
    CONFIG.declare(
        "energy_balance_type",
        ConfigValue(
            default=EnergyBalanceType.useDefault,
            domain=In(EnergyBalanceType),
            description="Energy balance construction flag",
            doc="""Indicates what type of energy balance should be constructed,
**default** - EnergyBalanceType.useDefault.
**Valid values:** {
**EnergyBalanceType.useDefault - refer to property package for default
balance type
**EnergyBalanceType.none** - exclude energy balances,
**EnergyBalanceType.enthalpyTotal** - single enthalpy balance for material,
**EnergyBalanceType.enthalpyPhase** - enthalpy balances for each phase,
**EnergyBalanceType.energyTotal** - single energy balance for material,
**EnergyBalanceType.energyPhase** - energy balances for each phase.}""",
        ),
    )
    CONFIG.declare(
        "momentum_balance_type",
        ConfigValue(
            default=MomentumBalanceType.pressureTotal,
            domain=In(MomentumBalanceType),
            description="Momentum balance construction flag",
            doc="""Indicates what type of momentum balance should be constructed,
**default** - MomentumBalanceType.pressureTotal.
**Valid values:** {
**MomentumBalanceType.none** - exclude momentum balances,
**MomentumBalanceType.pressureTotal** - single pressure balance for material,
**MomentumBalanceType.pressurePhase** - pressure balances for each phase,
**MomentumBalanceType.momentumTotal** - single momentum balance for material,
**MomentumBalanceType.momentumPhase** - momentum balances for each phase.}""",
        ),
    )
    CONFIG.declare(
        "has_heat_transfer",
        ConfigValue(
            default=False,
            domain=Bool,
            description="Heat transfer term construction flag",
            doc="""Indicates whether terms for heat transfer should be constructed,
**default** - False.
**Valid values:** {
**True** - include heat transfer terms,
**False** - exclude heat transfer terms.}""",
        ),
    )
    CONFIG.declare(
        "has_pressure_change",
        ConfigValue(
            default=False,
            domain=Bool,
            description="Pressure change term construction flag",
            doc="""Indicates whether terms for pressure change should be
constructed,
**default** - False.
**Valid values:** {
**True** - include pressure change terms,
**False** - exclude pressure change terms.}""",
        ),
    )
    CONFIG.declare(
        "has_equilibrium_reactions",
        ConfigValue(
            default=False,
            domain=Bool,
            description="Equilibrium reaction construction flag",
            doc="""Indicates whether terms for equilibrium controlled reactions
should be constructed,
**default** - True.
**Valid values:** {
**True** - include equilibrium reaction terms,
**False** - exclude equilibrium reaction terms.}""",
        ),
    )
    CONFIG.declare(
        "has_phase_equilibrium",
        ConfigValue(
            default=False,
            domain=Bool,
            description="Phase equilibrium construction flag",
            doc="""Indicates whether terms for phase equilibrium should be
constructed,
**default** = False.
**Valid values:** {
**True** - include phase equilibrium terms
**False** - exclude phase equilibrium terms.}""",
        ),
    )
    CONFIG.declare(
        "has_heat_of_reaction",
        ConfigValue(
            default=False,
            domain=Bool,
            description="Heat of reaction term construction flag",
            doc="""Indicates whether terms for heat of reaction terms should be
constructed,
**default** - False.
**Valid values:** {
**True** - include heat of reaction terms,
**False** - exclude heat of reaction terms.}""",
        ),
    )
    CONFIG.declare(
        "liquid_property_package",
        ConfigValue(
            default=useDefault,
            domain=is_physical_parameter_block,
            description="Property package to use for liquid phase",
            doc="""Property parameter object used to define property calculations
for the liquid phase,
**default** - useDefault.
**Valid values:** {
**useDefault** - use default package from parent model or flowsheet,
**PropertyParameterObject** - a PropertyParameterBlock object.}""",
        ),
    )
    CONFIG.declare(
        "liquid_property_package_args",
        ConfigBlock(
            implicit=True,
            description="Arguments to use for constructing liquid phase properties",
            doc="""A ConfigBlock with arguments to be passed to liquid phase
property block(s) and used when constructing these,
**default** - None.
**Valid values:** {
see property package for documentation.}""",
        ),
    )
    CONFIG.declare(
        "vapor_property_package",
        ConfigValue(
            default=useDefault,
            domain=is_physical_parameter_block,
            description="Property package to use for vapor phase",
            doc="""Property parameter object used to define property calculations
for the vapor phase,
**default** - useDefault.
**Valid values:** {
**useDefault** - use default package from parent model or flowsheet,
**PropertyParameterObject** - a PropertyParameterBlock object.}""",
        ),
    )
    CONFIG.declare(
        "vapor_property_package_args",
        ConfigBlock(
            implicit=True,
            description="Arguments to use for constructing vapor phase properties",
            doc="""A ConfigBlock with arguments to be passed to vapor phase
property block(s) and used when constructing these,
**default** - None.
**Valid values:** {
see property package for documentation.}""",
        ),
    )
    CONFIG.declare(
        "reaction_package",
        ConfigValue(
            default=None,
            domain=is_reaction_parameter_block,
            description="Reaction package to use for control volume",
            doc="""Reaction parameter object used to define reaction calculations,
**default** - None.
**Valid values:** {
**None** - no reaction package,
**ReactionParameterBlock** - a ReactionParameterBlock object.}""",
        ),
    )
    CONFIG.declare(
        "reaction_package_args",
        ConfigBlock(
            implicit=True,
            description="Arguments to use for constructing reaction packages",
            doc="""A ConfigBlock with arguments to be passed to a reaction block(s)
and used when constructing these,
**default** - None.
**Valid values:** {
see reaction package for documentation.}""",
        ),
    )

    def build(self):
        """
        Begin building model (pre-DAE transformation).
        Args:
            None
        Returns:
            None
        """
        # Call UnitModel.build to setup dynamics
        super(ADData, self).build()

        self.scaling_factor = Suffix(direction=Suffix.EXPORT)

        # Check phase lists match assumptions
        if self.config.vapor_property_package.phase_list != ["Vap"]:
            raise ConfigurationError(
                f"{self.name} Anaerobic digestor model requires that the vapor "
                f"phase property package have a single phase named 'Vap'"
            )
        if self.config.liquid_property_package.phase_list != ["Liq"]:
            raise ConfigurationError(
                f"{self.name} Anaerobic digestor model requires that the liquid "
                f"phase property package have a single phase named 'Liq'"
            )

        # Check for at least one common component in component lists
        if not any(
            j in self.config.vapor_property_package.component_list
            for j in self.config.liquid_property_package.component_list
        ):
            raise ConfigurationError(
                f"{self.name} Anaerobic digestor model requires that the liquid "
                f"and vapor phase property packages have at least one "
                f"common component."
            )

        self.liquid_phase = ControlVolume0DBlock(
            dynamic=self.config.dynamic,
            has_holdup=self.config.has_holdup,
            property_package=self.config.liquid_property_package,
            property_package_args=self.config.liquid_property_package_args,
            reaction_package=self.config.reaction_package,
            reaction_package_args=self.config.reaction_package_args,
        )

        self.liquid_phase.add_state_blocks(
            has_phase_equilibrium=self.config.has_phase_equilibrium
        )

        self.liquid_phase.add_reaction_blocks(
            has_equilibrium=self.config.has_equilibrium_reactions
        )

        # Separate liquid and vapor phases means that phase equilibrium will
        # be handled at the unit model level, thus has_phase_equilibrium is
        # False, but has_mass_transfer is True.
        self.liquid_phase.add_material_balances(
            balance_type=self.config.material_balance_type,
            has_rate_reactions=True,
            has_equilibrium_reactions=self.config.has_equilibrium_reactions,
            has_phase_equilibrium=self.config.has_phase_equilibrium,
            has_mass_transfer=True,
        )

        # Need to include enthalpy transfer term for the mass transfer
        self.liquid_phase.add_energy_balances(
            balance_type=self.config.energy_balance_type,
            has_heat_transfer=True,
            has_enthalpy_transfer=True,
        )

        self.liquid_phase.add_momentum_balances(
            balance_type=self.config.momentum_balance_type,
            has_pressure_change=self.config.has_pressure_change,
        )

        # ---------------------------------------------------------------------
        # Add single state block for vapor phase
        tmp_dict = dict(**self.config.vapor_property_package_args)
        tmp_dict["has_phase_equilibrium"] = False
        tmp_dict["defined_state"] = False
        self.vapor_phase = self.config.vapor_property_package.build_state_block(
            self.flowsheet().time, doc="Vapor phase properties", **tmp_dict
        )

        # ---------------------------------------------------------------------
        # Check flow basis is compatable
        # TODO : Could add code to convert flow bases, but not now
        t_init = self.flowsheet().time.first()
        if (
            self.vapor_phase[t_init].get_material_flow_basis()
            != self.liquid_phase.properties_out[t_init].get_material_flow_basis()
        ):
            raise ConfigurationError(
                f"{self.name} vapor and liquid property packages must use the "
                f"same material flow basis."
            )

        self.liquid_phase.add_geometry()

        # Add Ports
        self.add_inlet_port(name="inlet", block=self.liquid_phase, doc="Liquid feed")
        self.add_outlet_port(
            name="liquid_outlet", block=self.liquid_phase, doc="Bottoms stream"
        )
        self.add_outlet_port(
            name="vapor_outlet",
            block=self.vapor_phase,
            doc="Vapor stream from reactor",
        )

        # ---------------------------------------------------------------------
        # Add unit level constraints
        # First, need the union and intersection of component lists
        all_comps = (
            self.vapor_phase.component_list
            | self.liquid_phase.properties_out.component_list
        )
        common_comps = (
            self.vapor_phase.component_list
            & self.liquid_phase.properties_out.component_list
        )

        # Get units for unit conversion
        vunits = self.config.vapor_property_package.get_metadata().get_derived_units
        lunits = self.config.liquid_property_package.get_metadata().get_derived_units
        flow_basis = self.vapor_phase[t_init].get_material_flow_basis()
        if flow_basis == MaterialFlowBasis.molar:
            fb = "flow_mole"
        elif flow_basis == MaterialFlowBasis.mass:
            fb = "flow_mass"
        else:
            raise ConfigurationError(
                f"{self.name} AnaerobicDigestor only supports mass or molar "
                f"basis for MaterialFlowBasis."
            )

        # Add object references
        self.volume_liquid = Reference(self.liquid_phase.volume[:])

        self.volume_AD = Var(
            self.flowsheet().time,
            initialize=3700,
            units=lunits("volume"),
            doc="Total volume of anaerobic digestor",
        )

        self.volume_vapor = Var(
            self.flowsheet().time,
            initialize=300,
            units=lunits("volume"),
            doc="Volume of the gas",
        )

        self.k_p = Param(
            initialize=5e4,
            mutable=True,
            units=pyunits.m**3 / pyunits.day / pyunits.bar,
            doc="friction parameter",
        )

        self.KH_co2 = Var(
            self.flowsheet().time,
            initialize=0.02715,
            units=pyunits.kmol / pyunits.m**3 * pyunits.bar**-1,
            doc="CO2 Henry's law coefficient",
        )
        self.KH_ch4 = Var(
            self.flowsheet().time,
            initialize=0.00116,
            units=pyunits.kmol / pyunits.m**3 * pyunits.bar**-1,
            doc="CH4 Henry's law coefficient",
        )
        self.KH_h2 = Var(
            self.flowsheet().time,
            initialize=7.38e-4,
            units=pyunits.kmol / pyunits.m**3 * pyunits.bar**-1,
            doc="H2 Henry's law coefficient",
        )
        self.K_La = Param(
            initialize=200,
            units=pyunits.day**-1,
            mutable=True,
            doc="Gas-liquid transfer coefficient",
        )
        self.electricity_consumption = Var(
            self.flowsheet().time,
            units=pyunits.kW,
            bounds=(0, None),
            doc="Electricity consumption of unit",
        )
        # The value is taken from Maravelias' data
        self.energy_electric_flow_vol_inlet = Param(
            initialize=0.029,
            units=pyunits.kWh / pyunits.m**3,
            mutable=True,
            doc="Electricity intensity with respect to inlet flow",
        )

        def CO2_Henrys_law_rule(self, t):
            return (
                self.KH_co2[t]
                == (
                    0.035
                    * exp(
                        -19410
                        / pyunits.mole
                        * pyunits.joule
                        / (Constants.gas_constant)
                        * (
                            (1 / self.config.vapor_property_package.temperature_ref)
                            - (1 / self.vapor_phase[t].temperature)
                        )
                    )
                )
                * pyunits.kilomole
                / pyunits.bar
                / pyunits.meter**3
            )

        self.CO2_Henrys_law = Constraint(
            self.flowsheet().time,
            rule=CO2_Henrys_law_rule,
            doc="CO2 Henry's law coefficient constraint",
        )

        def Ch4_Henrys_law_rule(self, t):
            return (
                self.KH_ch4[t]
                == (
                    0.0014
                    * exp(
                        -14240
                        / pyunits.mole
                        * pyunits.joule
                        / (Constants.gas_constant)
                        * (
                            (1 / self.config.vapor_property_package.temperature_ref)
                            - (1 / self.vapor_phase[t].temperature)
                        )
                    )
                )
                * pyunits.kilomole
                / pyunits.bar
                / pyunits.meter**3
            )

        self.Ch4_Henrys_law = Constraint(
            self.flowsheet().time,
            rule=Ch4_Henrys_law_rule,
            doc="Ch4 Henry's law coefficient constraint",
        )

        def H2_Henrys_law_rule(self, t):
            return (
                self.KH_h2[t]
                == (
                    7.8e-4
                    * exp(
                        -4180
                        / pyunits.mole
                        * pyunits.joule
                        / (Constants.gas_constant)
                        * (
                            (1 / self.config.vapor_property_package.temperature_ref)
                            - (1 / self.vapor_phase[t].temperature)
                        )
                    )
                )
                * pyunits.kilomole
                / pyunits.bar
                / pyunits.meter**3
            )

        self.H2_Henrys_law = Constraint(
            self.flowsheet().time,
            rule=H2_Henrys_law_rule,
            doc="H2 Henry's law coefficient constraint",
        )

        def outlet_P_rule(self, t):
            return self.vapor_phase[t].pressure == (
                sum(
                    self.vapor_phase[t].pressure_sat[j]
                    for j in self.config.vapor_property_package.component_list
                )
            )

        self.outlet_P = Constraint(
            self.flowsheet().time,
            rule=outlet_P_rule,
            doc="Outlet vapor phase pressure",
        )

        # Material balances
        def rule_material_balance(self, t, j):
            if j in common_comps:
                # Component is in equilibrium
                # Mass transfer equals vapor flowrate
                return self.liquid_phase.mass_transfer_term[
                    t, "Liq", j
                ] == -self.vapor_phase[t].get_material_flow_terms("Vap", j)

            elif j == "S_IC":
                # Mass transfer term is zero, no vapor flowrate
                return self.liquid_phase.mass_transfer_term[
                    t, "Liq", j
                ] == -self.vapor_phase[t].get_material_flow_terms("Vap", "S_co2")

            elif j in self.liquid_phase.properties_out.component_list:
                # No mass transfer term
                # Set vapor flowrate to an arbitary small value
                return self.liquid_phase.mass_transfer_term[
                    t, "Liq", j
                ] == 1e-8 * lunits(fb)

        self.unit_material_balance = Constraint(
            self.flowsheet().time,
            self.liquid_phase.properties_out.component_list,
            rule=rule_material_balance,
            doc="Unit level material balances",
        )

        def Sh2_conc_rule(self, t):
            return self.liquid_phase.mass_transfer_term[t, "Liq", "S_h2"] == -1 * (
                pyunits.convert(self.K_La, to_units=1 / pyunits.s)
                * (
                    self.liquid_phase.properties_out[t].conc_mass_comp["S_h2"]
                    - 16
                    * pyunits.kg
                    / pyunits.kmol
                    * pyunits.convert(
                        self.KH_h2[t],
                        to_units=pyunits.kmol / pyunits.m**3 * pyunits.Pa**-1,
                    )
                    * self.vapor_phase[t].pressure_sat["S_h2"]
                )
                * self.volume_liquid[t]
            )

        self.Sh2_conc = Constraint(
            self.flowsheet().time,
            rule=Sh2_conc_rule,
            doc="Mass transfer rate of H2 gas vap",
        )

        def Sch4_conc_rule(self, t):
            return self.liquid_phase.mass_transfer_term[t, "Liq", "S_ch4"] == -1 * (
                pyunits.convert(self.K_La, to_units=1 / pyunits.s)
                * (
                    self.liquid_phase.properties_out[t].conc_mass_comp["S_ch4"]
                    - 64
                    * pyunits.kg
                    / pyunits.kmol
                    * pyunits.convert(
                        self.KH_ch4[t],
                        to_units=pyunits.kmol / pyunits.m**3 * pyunits.Pa**-1,
                    )
                    * self.vapor_phase[t].pressure_sat["S_ch4"]
                )
                * self.volume_liquid[t]
            )

        self.Sch4_conc = Constraint(
            self.flowsheet().time,
            rule=Sch4_conc_rule,
            doc="Mass transfer rate of CH4 gas vap",
        )

        def Sco2_conc_rule(self, t):
            return self.liquid_phase.mass_transfer_term[t, "Liq", "S_IC"] == -1 * (
                pyunits.convert(self.K_La, to_units=1 / pyunits.s)
                * (
                    self.liquid_phase.reactions[t].conc_mol_co2
                    - pyunits.convert(
                        self.KH_co2[t],
                        to_units=pyunits.kmol / pyunits.m**3 * pyunits.Pa**-1,
                    )
                    * self.vapor_phase[t].pressure_sat["S_co2"]
                )
                * self.volume_liquid[t]
            ) * (1 * pyunits.kg / pyunits.kmole)

        self.Sco2_conc = Constraint(
            self.flowsheet().time,
            rule=Sco2_conc_rule,
            doc="Mass transfer rate of CO2 gas vap",
        )

        def flow_vol_vap_rule(self, t):
            return self.vapor_phase[t].flow_vol == (
                pyunits.convert(
                    self.k_p, to_units=pyunits.m**3 / pyunits.s / pyunits.Pa
                )
                * (self.vapor_phase[t].pressure - 101325 * pyunits.Pa)
            ) * (self.vapor_phase[t].pressure / (101325 * pyunits.Pa))

        self.flow_vol_vap = Constraint(
            self.flowsheet().time,
            rule=flow_vol_vap_rule,
            doc="Vol flow",
        )

        def ad_total_volume_rule(self, t):
            return self.volume_AD[t] == (self.volume_liquid[t] + self.volume_vapor[t])

        self.ad_total_volume = Constraint(
            self.flowsheet().time,
            rule=ad_total_volume_rule,
            doc="Total anaerobic digestor volume",
        )
        # Add AD performance equation
        def ad_performance_eqn_rule(self, t, r):
            return self.liquid_phase.rate_reaction_extent[t, r] == (
                self.volume_liquid[t] * self.liquid_phase.reactions[t].reaction_rate[r]
            )

        self.ad_performance_eqn = Constraint(
            self.flowsheet().time,
            self.config.reaction_package.rate_reaction_idx,
            rule=ad_performance_eqn_rule,
            doc="AD performance equation",
        )
        # Temperature equality constraint
        def rule_temperature_balance(self, t):
            return self.liquid_phase.properties_out[t].temperature == pyunits.convert(
                self.vapor_phase[t].temperature, to_units=lunits("temperature")
            )

        self.unit_temperature_equality = Constraint(
            self.flowsheet().time,
            rule=rule_temperature_balance,
            doc="Unit level temperature equality",
        )

        if (
            self.config.has_pressure_change is True
            and self.config.momentum_balance_type != MomentumBalanceType.none
        ):
            self.deltaP = Reference(self.liquid_phase.deltaP[:])

            # Pressure balance constraint
            def rule_pressure_balance(self, t):
                return (
                    self.deltaP[t]
                    == self.vapor_outlet.pressure[t]
                    - self.liquid_phase.properties_in[t].pressure
                )

            self.unit_pressure_balance = Constraint(
                self.flowsheet().time,
                rule=rule_pressure_balance,
                doc="Unit level pressure balance",
            )

        # Unit level energy balance
        # Energy leaving in vapor phase must be equal and opposite to enthalpy
        # transfer from liquid phase
        def rule_energy_balance(self, t):
            return self.liquid_phase.enthalpy_transfer[
                t
            ] + self.liquid_phase.properties_in[t].get_enthalpy_flow_terms(
                "Liq"
            ) == self.liquid_phase.properties_out[
                t
            ].get_enthalpy_flow_terms(
                "Liq"
            ) + self.vapor_phase[
                t
            ].get_enthalpy_flow_terms(
                "Vap"
            )

        self.unit_enthalpy_balance = Constraint(
            self.flowsheet().time,
            rule=rule_energy_balance,
            doc="Unit level enthalpy_balance",
        )

        # Electricity constraint
        def rule_electricity_consumption(self, t):
            return self.electricity_consumption[t] == (
                self.energy_electric_flow_vol_inlet
                * pyunits.convert(
                    self.liquid_phase.properties_in[t].flow_vol,
                    to_units=pyunits.m**3 / pyunits.hr,
                )
            )

        self.unit_electricity_consumption = Constraint(
            self.flowsheet().time,
            rule=rule_electricity_consumption,
            doc="Unit level electricity consumption",
        )

        # Set references to balance terms at unit level
        self.heat_duty = Reference(self.liquid_phase.heat[:])

        iscale.set_scaling_factor(self.volume_AD, 1e-2)
        iscale.set_scaling_factor(self.volume_vapor, 1e-2)
        iscale.set_scaling_factor(self.liquid_phase.rate_reaction_generation, 1e4)
        iscale.set_scaling_factor(self.liquid_phase.mass_transfer_term, 1e2)
        iscale.set_scaling_factor(self.liquid_phase.heat, 1e-2)
        iscale.set_scaling_factor(self.liquid_phase.rate_reaction_extent, 1e2)
        iscale.set_scaling_factor(self.liquid_phase.enthalpy_transfer, 1e-4)
        iscale.set_scaling_factor(self.liquid_phase.volume, 1e-2)
        iscale.set_scaling_factor(self.KH_co2, 1e2)
        iscale.set_scaling_factor(self.KH_ch4, 1e3)
        iscale.set_scaling_factor(self.KH_h2, 1e4)
        iscale.set_scaling_factor(self.electricity_consumption, 1e0)

    def _get_stream_table_contents(self, time_point=0):
        return create_stream_table_dataframe(
            {
                "Liquid Inlet": self.inlet,
                "Liquid Outlet": self.liquid_outlet,
                "Vapor Outlet": self.vapor_outlet,
            },
            time_point=time_point,
        )

    def _get_performance_contents(self, time_point=0):
        # TODO: add aggregated quantities/key metrics
        var_dict = {"Volume": self.volume_AD[time_point]}
        if hasattr(self, "heat_duty"):
            var_dict["Heat Duty"] = self.heat_duty[time_point]
        if hasattr(self, "deltaP"):
            var_dict["Pressure Change"] = self.deltaP[time_point]

        return {"vars": var_dict}

    def calculate_scaling_factors(self):
        super().calculate_scaling_factors()

        common_comps = (
            self.vapor_phase.component_list
            & self.liquid_phase.properties_out.component_list
        )

        # TODO: improve this later; for now, this resolved some scaling issues for modified adm1 test file
        if "S_IP" in self.config.liquid_property_package.component_list:
            iscale.set_scaling_factor(self.liquid_phase.heat, 1e-6)
            iscale.set_scaling_factor(
                self.liquid_phase.properties_out[0].conc_mass_comp["S_IP"], 1e-5
            )
            iscale.set_scaling_factor(
                self.liquid_phase.properties_out[0].conc_mass_comp["S_IN"], 1e-5
            )

        for t, v in self.flow_vol_vap.items():
            iscale.constraint_scaling_transform(
                v,
                iscale.get_scaling_factor(
                    self.liquid_phase.properties_out[t].flow_vol,
                    default=1,
                    warning=True,
                ),
            )

        for (t, j), v in self.unit_material_balance.items():
            if j in common_comps:
                iscale.constraint_scaling_transform(
                    v,
                    iscale.get_scaling_factor(
                        self.liquid_phase.mass_transfer_term[t, "Liq", j],
                        default=1,
                        warning=True,
                    ),
                )
            else:
                pass  # no need to scale this constraint

        for t, v in self.unit_temperature_equality.items():
            iscale.constraint_scaling_transform(
                v,
                iscale.get_scaling_factor(
                    self.liquid_phase.properties_out[t].temperature,
                    default=1,
                    warning=True,
                ),
            )

        for t, v in self.unit_enthalpy_balance.items():
            iscale.constraint_scaling_transform(
                v,
                iscale.get_scaling_factor(
                    self.liquid_phase.enthalpy_transfer[t], default=1, warning=True
                ),
            )

        for t, v in self.outlet_P.items():
            iscale.constraint_scaling_transform(
                v,
                iscale.get_scaling_factor(
                    self.liquid_phase.properties_out[t].pressure,
                    default=1,
                    warning=True,
                ),
            )
        for t, v in self.Sh2_conc.items():
            iscale.constraint_scaling_transform(
                v,
                iscale.get_scaling_factor(
                    self.liquid_phase.properties_out[t].conc_mass_comp["S_h2"],
                    default=1,
                    warning=True,
                ),
            )
        for t, v in self.Sch4_conc.items():
            iscale.constraint_scaling_transform(
                v,
                iscale.get_scaling_factor(
                    self.liquid_phase.properties_out[t].conc_mass_comp["S_ch4"],
                    default=1,
                    warning=True,
                ),
            )
        for t, v in self.Sco2_conc.items():
            iscale.constraint_scaling_transform(
                v,
                iscale.get_scaling_factor(
                    self.liquid_phase.properties_out[t].conc_mass_comp["S_ch4"],
                    default=1,
                    warning=True,
                ),
            )

    # TO DO: fix initialization
    def initialize_build(
        self,
        liquid_state_args=None,
        vapor_state_args=None,
        outlvl=idaeslog.NOTSET,
        solver=None,
        optarg=None,
    ):
        """
        Initialization routine for anaerobic digestor unit model.

        Keyword Arguments:
            liquid_state_args : a dict of arguments to be passed to the
                liquid property packages to provide an initial state for
                initialization (see documentation of the specific property
                package) (default = none).
            vapor_state_args : a dict of arguments to be passed to the
                vapor property package to provide an initial state for
                initialization (see documentation of the specific property
                package) (default = none).
            outlvl : sets output level of initialization routine
            optarg : solver options dictionary object (default=None, use
                     default solver options)
            solver : str indicating which solver to use during
                     initialization (default = None, use default IDAES solver)

        Returns:
            None
        """
        if optarg is None:
            optarg = {}

        # Check DOF
        if degrees_of_freedom(self) != 0:
            raise InitializationError(
                f"{self.name} degrees of freedom were not 0 at the beginning "
                f"of initialization. DoF = {degrees_of_freedom(self)}"
            )

        # Set solver options
        init_log = idaeslog.getInitLogger(self.name, outlvl, tag="unit")
        solve_log = idaeslog.getSolveLogger(self.name, outlvl, tag="unit")

        solverobj = get_solver(solver, optarg)

        # ---------------------------------------------------------------------
        # Initialize liquid phase control volume block
        flags = self.liquid_phase.initialize(
            outlvl=outlvl,
            optarg=optarg,
            solver=solver,
            state_args=liquid_state_args,
            hold_state=True,
        )

        init_log.info_high("Initialization Step 1 Complete.")
        # ---------------------------------------------------------------------
        # Initialize vapor phase state block
        if vapor_state_args is None:
            t_init = self.flowsheet().time.first()
            vapor_state_args = {}
            vap_state_vars = self.vapor_phase[t_init].define_state_vars()

            liq_state = self.liquid_phase.properties_out[t_init]

            # Check for unindexed state variables
            for sv in vap_state_vars:
                if "flow" in sv:
                    vapor_state_args[sv] = 13 * value(getattr(liq_state, sv))
                elif "conc" in sv:
                    # Flow is indexed by component
                    vapor_state_args[sv] = {}
                    for j in vap_state_vars[sv]:
                        if j in liq_state.component_list:
                            vapor_state_args[sv][j] = 1e3 * value(
                                getattr(liq_state, sv)[j]
                            )
                        else:
                            vapor_state_args[sv][j] = 0.5

                elif "pressure" in sv:
                    vapor_state_args[sv] = 1.05 * value(getattr(liq_state, sv))

                else:
                    vapor_state_args[sv] = value(getattr(liq_state, sv))

        self.vapor_phase.initialize(
            outlvl=outlvl,
            optarg=optarg,
            solver=solver,
            state_args=vapor_state_args,
            hold_state=False,
        )

        init_log.info_high("Initialization Step 2 Complete.")

        # ---------------------------------------------------------------------
        # # Solve unit model
        with idaeslog.solver_log(solve_log, idaeslog.DEBUG) as slc:
            results = solverobj.solve(self, tee=slc.tee)
            if not check_optimal_termination(results):
                init_log.warning(
                    f"Trouble solving unit model {self.name}, trying one more time"
                )
                results = solverobj.solve(self, tee=slc.tee)
        init_log.info_high(
            "Initialization Step 3 {}.".format(idaeslog.condition(results))
        )

        # ---------------------------------------------------------------------
        # Release Inlet state
        self.liquid_phase.release_state(flags, outlvl)

        if not check_optimal_termination(results):
            raise InitializationError(
                f"{self.name} failed to initialize successfully. Please check "
                f"the output logs for more information."
            )

        init_log.info("Initialization Complete: {}".format(idaeslog.condition(results)))
